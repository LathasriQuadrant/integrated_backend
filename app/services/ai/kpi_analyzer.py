 
"""
KPI Intelligence analyzer.
 
Collects every KPI candidate across all workbooks and sends them to
OpenAI in a single call to detect duplicates, near-duplicates, and
thematic clusters. Run globally (not per-workbook) since duplication is
inherently a cross-workbook concern.
"""
 
from __future__ import annotations
 
import json
from typing import Any
 
from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import KPI_INTELLIGENCE_SYSTEM_PROMPT
from app.utils.naming import build_datasource_name_lookup, resolve_name
 
 
async def analyze_kpis(
    client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
) -> dict[str, Any]:
    all_kpis = []
    for bundle in workbook_bundles:
        workbook_name = bundle["workbook_metadata"]["name"]
        ds_lookup = build_datasource_name_lookup(bundle)
        for kpi in bundle.get("kpis", []):
            # kpi["name"] is already a caption (see
            # app.services.discovery.kpi_service), but kpi["datasource"]
            # is still the internal Tableau name -- translate it too so
            # nothing opaque reaches the model or the response.
            all_kpis.append(
                {
                    **kpi,
                    "datasource": resolve_name(kpi.get("datasource", ""), ds_lookup),
                    "workbook": workbook_name,
                }
            )
 
    if not all_kpis:
        return {"duplicate_kpis": [], "similar_kpis": [], "kpi_clusters": []}
 
    response = await client.complete_json(
        system_prompt=KPI_INTELLIGENCE_SYSTEM_PROMPT,
        user_prompt=json.dumps({"kpis": all_kpis}, default=str),
    )
 
    return {
        "duplicate_kpis": response.get("duplicate_kpis", []),
        "similar_kpis": response.get("similar_kpis", []),
        "kpi_clusters": response.get("kpi_clusters", []),
    }