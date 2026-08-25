 
"""
Unused Component analyzer.
 
For every workbook, sends its full component inventory plus reference
data (which fields/datasources worksheets actually use) to OpenAI to
flag likely-unused assets ahead of migration.
"""
 
from __future__ import annotations
 
import json
from typing import Any
 
from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import UNUSED_COMPONENT_SYSTEM_PROMPT
from app.utils.naming import build_datasource_name_lookup, build_field_name_lookup, resolve_name
 
 
async def analyze_unused_components(
    client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
) -> dict[str, Any]:
    aggregate = {
        "unused_worksheets": [],
        "unused_calculated_fields": [],
        "unused_filters": [],
        "unused_parameters": [],
        "unused_datasources": [],
    }
 
    for bundle in workbook_bundles:
        workbook_name = bundle["workbook_metadata"]["name"]
 
        field_lookup = build_field_name_lookup(bundle)
        ds_lookup = build_datasource_name_lookup(bundle)
 
        # Filters reference fields by internal name (e.g.
        # "Calculation_0014397469077547"); translate before sending so
        # the model reasons over readable names rather than echoing
        # opaque ids straight through.
        readable_filters = [
            {**f, "column": resolve_name(f.get("column", ""), field_lookup)}
            for f in bundle.get("components", {}).get("filters", [])
        ]
 
        payload = {
            "workbook_name": workbook_name,
            "worksheets": bundle.get("components", {}).get("worksheets", []),
            "dashboards": bundle.get("components", {}).get("dashboards", []),
            "calculated_fields": bundle.get("fields", {}).get("calculated_fields", []),
            "filters": readable_filters,
            "parameters": bundle.get("components", {}).get("parameters", []),
            "datasources": bundle.get("data_model", {}).get("datasources", []),
        }
 
        response = await client.complete_json(
            system_prompt=UNUSED_COMPONENT_SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, default=str),
        )
 
        # Belt-and-suspenders: even though the payload above already used
        # readable names, don't rely on the model preserving that --
        # translate every returned "name" through the same lookups before
        # it reaches the response, so an internal id can never surface in
        # a UI even if the model echoes a raw field/datasource dict key.
        name_resolvers = {
            "unused_worksheets": lambda n: n,  # already real names at discovery time
            "unused_calculated_fields": lambda n: resolve_name(n, field_lookup),
            "unused_filters": lambda n: resolve_name(n, field_lookup),
            "unused_parameters": lambda n: resolve_name(n, field_lookup),
            "unused_datasources": lambda n: resolve_name(n, ds_lookup),
        }
 
        for key in aggregate:
            resolver = name_resolvers[key]
            for item in response.get(key, []):
                resolved_item = {**item, "name": resolver(item.get("name", ""))}
                aggregate[key].append({"workbook": workbook_name, **resolved_item})
 
    return aggregate