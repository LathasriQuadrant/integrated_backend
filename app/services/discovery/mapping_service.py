"""
Data-model-to-report mapping layer.
 
Builds the datasource -> reports and dashboard -> datasources maps across
*all* discovered workbooks, plus aggregate mapping metrics (shared
datasources/tables). This runs once, after every workbook has been
individually discovered, since it needs the full picture to compute
cross-workbook sharing.
"""
 
from __future__ import annotations
 
from collections import defaultdict
from typing import Any
 
 
def build_mappings(workbook_bundles: list[dict[str, Any]]) -> dict[str, Any]:
    datasource_to_reports: dict[str, set[str]] = defaultdict(set)
    dashboard_to_datasources: dict[str, set[str]] = defaultdict(set)
    table_usage: dict[str, set[str]] = defaultdict(set)
 
    for bundle in workbook_bundles:
        workbook_name = bundle["workbook_metadata"]["name"]
        # Key everything by the datasource's human-readable caption, not
        # its internal Tableau name (e.g. "federated.12p60851wjjcxx...").
        # This mapping layer feeds directly into the AI analyzers'
        # output, so an opaque internal id here means an opaque id shows
        # up in a UI later. Falls back to the internal name only if no
        # caption was captured.
        datasource_names = [ds.get("caption") or ds["name"] for ds in bundle["data_model"]["datasources"]]
 
        # "Reports" means dashboards and worksheets -- the actual
        # consumable artifacts an analyst opens -- NOT the parent
        # workbook. Including the workbook name here would double-count
        # every datasource against both its workbook and each dashboard
        # inside that workbook, inflating reports_per_datasource.
        report_names: set[str] = set()
        for dashboard in bundle["reports"]["dashboards"]:
            report_names.add(dashboard.get("name", ""))
        for worksheet in bundle["reports"]["worksheets"]:
            report_names.add(worksheet.get("name", ""))
        report_names.discard("")
 
        for dashboard in bundle["reports"]["dashboards"]:
            dashboard_name = dashboard.get("name", "")
            for ds_name in datasource_names:
                dashboard_to_datasources[dashboard_name].add(ds_name)
 
        for ds_name in datasource_names:
            datasource_to_reports[ds_name].update(report_names)
            if not report_names:
                # No dashboards/worksheets were discovered for this
                # workbook (e.g. a datasource-only publish) -- fall back
                # to naming the workbook itself so the datasource still
                # shows up as "used by something".
                datasource_to_reports[ds_name].add(workbook_name)
 
        for table in bundle["data_model"]["tables"]:
            # Same reasoning: resolve the table's owning datasource to a
            # caption rather than leaking the internal name into
            # table_usage (used below to compute shared_tables).
            owning_datasource = table.get("datasource", "")
            ds_caption = next(
                (ds.get("caption") or ds["name"] for ds in bundle["data_model"]["datasources"] if ds["name"] == owning_datasource),
                owning_datasource,
            )
            table_key = f"{ds_caption}.{table.get('table', table.get('name', ''))}"
            table_usage[table_key].add(workbook_name)
 
    datasource_to_reports_out = {k: sorted(v) for k, v in datasource_to_reports.items()}
    dashboard_to_datasources_out = {k: sorted(v) for k, v in dashboard_to_datasources.items()}
 
    reports_per_datasource = {k: len(v) for k, v in datasource_to_reports_out.items()}
    datasources_per_dashboard = {k: len(v) for k, v in dashboard_to_datasources_out.items()}
 
    shared_datasources_count = sum(1 for v in datasource_to_reports_out.values() if len(v) > 1)
    shared_tables_count = sum(1 for v in table_usage.values() if len(v) > 1)
 
    return {
        "datasource_to_reports": datasource_to_reports_out,
        "dashboard_to_datasources": dashboard_to_datasources_out,
        "mapping_metrics": {
            "reports_per_datasource": reports_per_datasource,
            "datasources_per_dashboard": datasources_per_dashboard,
            "shared_datasources": shared_datasources_count,
            "shared_tables": shared_tables_count,
        },
    }
 
 
def per_workbook_mapping(bundle: dict[str, Any], global_mappings: dict[str, Any]) -> dict[str, Any]:
    """Slice the global mapping down to just what's relevant for one workbook,
    for embedding inside that workbook's own bundle."""
 
    datasource_names = {ds.get("caption") or ds["name"] for ds in bundle["data_model"]["datasources"]}
    dashboard_names = {d.get("name", "") for d in bundle["reports"]["dashboards"]}
 
    datasource_to_reports = {
        k: v for k, v in global_mappings["datasource_to_reports"].items() if k in datasource_names
    }
    dashboard_to_datasources = {
        k: v for k, v in global_mappings["dashboard_to_datasources"].items() if k in dashboard_names
    }
 
    return {
        "datasource_to_reports": datasource_to_reports,
        "dashboard_to_datasources": dashboard_to_datasources,
        "mapping_metrics": {
            "reports_per_datasource": {
                k: v for k, v in global_mappings["mapping_metrics"]["reports_per_datasource"].items() if k in datasource_names
            },
            "datasources_per_dashboard": {
                k: v for k, v in global_mappings["mapping_metrics"]["datasources_per_dashboard"].items() if k in dashboard_names
            },
            "shared_datasources": global_mappings["mapping_metrics"]["shared_datasources"],
            "shared_tables": global_mappings["mapping_metrics"]["shared_tables"],
        },
    }