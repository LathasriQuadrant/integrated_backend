"""
Shared Data Model analyzer.

`shared_datasources` and `shared_tables` are answered with plain counting
-- "is this used by more than one report/datasource" is exact arithmetic
on the mapping data, not a judgment call -- so they're computed directly
in Python instead of asked of the model. Handing that yes/no threshold to
the LLM was producing inconsistent results between otherwise-identical
runs. The model is only used for "recommended_semantic_models", which
genuinely needs judgment (how Tableau datasources should logically group
into Power BI semantic models), and is given the computed sharing facts
as input so its groupings can lean on them.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import SHARED_DATA_MODEL_SYSTEM_PROMPT
from app.utils.naming import build_datasource_name_lookup


def _compute_shared_datasources(datasource_to_reports: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [
        {
            "datasource": ds_name,
            "used_by_report_count": len(reports),
            "used_by_reports": sorted(reports),
        }
        for ds_name, reports in datasource_to_reports.items()
        if len(reports) > 1
    ]


def _compute_shared_tables(table_to_datasources: dict[str, set[str]]) -> list[dict[str, Any]]:
    return [
        {
            "table": table_name,
            "used_by_datasource_count": len(datasources),
            "used_by_datasources": sorted(datasources),
        }
        for table_name, datasources in table_to_datasources.items()
        if len(datasources) > 1
    ]


async def analyze_shared_data_model(
    client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
) -> dict[str, Any]:
    datasource_to_reports: dict[str, list[str]] = {}
    table_to_datasources: dict[str, set[str]] = {}

    for bundle in workbook_bundles:
        # mappings.datasource_to_reports is already keyed by datasource
        # caption (see app.services.discovery.mapping_service) -- nothing
        # to translate there. table_to_datasources below is built
        # independently straight from data_model.tables, though, which
        # only carries the internal datasource name -- resolve that here
        # so it doesn't leak an opaque id into the LLM payload/response.
        ds_name_lookup = build_datasource_name_lookup(bundle)

        mappings = bundle.get("mappings", {})
        for ds_name, reports in mappings.get("datasource_to_reports", {}).items():
            existing = set(datasource_to_reports.get(ds_name, []))
            existing.update(reports)
            datasource_to_reports[ds_name] = sorted(existing)

        for table in bundle.get("data_model", {}).get("tables", []):
            table_name = table.get("table") or table.get("name", "")
            owning_datasource = ds_name_lookup.get(table.get("datasource", ""), table.get("datasource", ""))
            table_to_datasources.setdefault(table_name, set()).add(owning_datasource)

    if not datasource_to_reports and not table_to_datasources:
        return {"shared_datasources": [], "shared_tables": [], "recommended_semantic_models": []}

    shared_datasources = _compute_shared_datasources(datasource_to_reports)
    shared_tables = _compute_shared_tables(table_to_datasources)

    payload = {
        "datasource_to_reports": datasource_to_reports,
        "table_to_datasources": {k: sorted(v) for k, v in table_to_datasources.items()},
        # Already-computed sharing facts, handed in as context rather than
        # asked for -- see the module docstring.
        "shared_datasources": shared_datasources,
        "shared_tables": shared_tables,
    }

    response = await client.complete_json(
        system_prompt=SHARED_DATA_MODEL_SYSTEM_PROMPT,
        user_prompt=json.dumps(payload, default=str),
    )

    return {
        "shared_datasources": shared_datasources,
        "shared_tables": shared_tables,
        "recommended_semantic_models": response.get("recommended_semantic_models", []),
    }