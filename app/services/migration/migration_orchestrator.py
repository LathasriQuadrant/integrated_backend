"""
Orchestrates the end-to-end Tableau -> Power BI migration for a single
workbook: build the semantic model (TMSL), translate calculated fields to
DAX measures, deploy to Fabric, build the report (PBIR-Legacy) bound to
that semantic model, deploy it, and produce a reconciliation summary.
"""

from __future__ import annotations

from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.migration.fabric_auth import get_fabric_access_token
from app.services.migration.fabric_client import FabricClient
from app.services.migration.report_builder import build_report_json
from app.services.migration.tmsl_builder import build_measures, build_tmsl_model, merge_measures_into_model


async def migrate_semantic_model(
    fabric_credentials, workspace_id: str, semantic_model_name: str, workbook_bundle: dict[str, Any],
    translate_calculated_fields: bool,
) -> dict[str, Any]:
    build_result = build_tmsl_model(workbook_bundle, semantic_model_name)
    model_bim = build_result["model_bim"]
    hub_table = build_result["hub_table"]
    known_table_names = {t["name"] for t in model_bim["model"]["tables"]}

    ai_client = OpenAIAnalysisClient()
    measures_by_table, translations = await build_measures(
        ai_client, workbook_bundle, hub_table, known_table_names, translate_calculated_fields
    )
    merge_measures_into_model(model_bim, measures_by_table)

    access_token = await get_fabric_access_token(fabric_credentials)
    fabric_client = FabricClient(access_token)

    workbook_name = workbook_bundle.get("workbook_metadata", {}).get("name", "")
    semantic_model_id = await fabric_client.create_semantic_model(
        workspace_id=workspace_id,
        display_name=semantic_model_name,
        description=f"Migrated from Tableau workbook '{workbook_name}' via automated pre-migration platform.",
        model_bim=model_bim,
    )

    return {
        "semantic_model_id": semantic_model_id,
        "semantic_model_name": semantic_model_name,
        "tables_deployed": [t["name"] for t in model_bim["model"]["tables"]],
        "relationships_deployed": len(model_bim["model"]["relationships"]),
        "relationships_needing_review": build_result["relationships_needing_review"],
        "measures": translations,
        "warnings": build_result["warnings"],
        "hub_table": hub_table,
    }


async def migrate_report(
    fabric_credentials, workspace_id: str, report_name: str, semantic_model_id: str,
    workbook_bundle: dict[str, Any], hub_table: str,
) -> dict[str, Any]:
    report_json, visual_mappings = build_report_json(workbook_bundle, hub_table)

    access_token = await get_fabric_access_token(fabric_credentials)
    fabric_client = FabricClient(access_token)

    workbook_name = workbook_bundle.get("workbook_metadata", {}).get("name", "")
    report_id = await fabric_client.create_report(
        workspace_id=workspace_id,
        display_name=report_name,
        description=f"Migrated from Tableau workbook '{workbook_name}' via automated pre-migration platform.",
        report_json=report_json,
        semantic_model_id=semantic_model_id,
    )

    warnings = []
    unsupported = [m["tableau_worksheet"] for m in visual_mappings if m["coverage"] == "unsupported"]
    if unsupported:
        warnings.append(
            f"{len(unsupported)} worksheet(s) used a Tableau mark type with no Power BI equivalent and "
            f"were rendered as tables instead: {', '.join(unsupported)}"
        )

    return {
        "report_id": report_id,
        "report_name": report_name,
        "pages_deployed": [s["displayName"] for s in report_json["sections"]],
        "visual_mappings": visual_mappings,
        "warnings": warnings,
    }


def build_validation_summary(
    workbook_bundle: dict[str, Any],
    semantic_model_result: dict[str, Any],
    report_result: dict[str, Any] | None,
) -> dict[str, Any]:
    workbook_name = workbook_bundle.get("workbook_metadata", {}).get("name", "")
    source_tables = workbook_bundle.get("data_model", {}).get("tables", [])
    source_measures = workbook_bundle.get("fields", {}).get("calculated_fields", [])
    source_dashboards = workbook_bundle.get("components", {}).get("dashboards", [])
    source_worksheets = workbook_bundle.get("components", {}).get("worksheets", [])

    measures_needing_review = sum(1 for m in semantic_model_result["measures"] if m["needs_review"])

    pages_deployed_count = len(report_result["pages_deployed"]) if report_result else 0
    visuals_deployed_count = len(report_result["visual_mappings"]) if report_result else 0
    visuals_unsupported = (
        [m["tableau_worksheet"] for m in report_result["visual_mappings"] if m["coverage"] == "unsupported"]
        if report_result
        else []
    )

    if report_result is None:
        overall_status = "partial"
    elif measures_needing_review > 0 or visuals_unsupported:
        overall_status = "ready_for_review"
    else:
        overall_status = "ready_for_review"  # migrations always warrant a human check before go-live

    return {
        "source_workbook_name": workbook_name,
        "tables_source_count": len(source_tables),
        "tables_deployed_count": len(semantic_model_result["tables_deployed"]),
        "measures_source_count": len(source_measures),
        "measures_deployed_count": len(semantic_model_result["measures"]),
        "measures_needing_review_count": measures_needing_review,
        "dashboards_source_count": len(source_dashboards),
        "pages_deployed_count": pages_deployed_count,
        "worksheets_source_count": len(source_worksheets),
        "visuals_deployed_count": visuals_deployed_count,
        "visuals_unsupported": visuals_unsupported,
        "overall_status": overall_status,
    }
