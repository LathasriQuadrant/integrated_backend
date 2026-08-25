"""
Builds a Power BI report definition (PBIR-Legacy `report.json`) from one
workbook's normalized Tableau metadata, mapping each Tableau dashboard to
a report page and each worksheet placed on it to a visual.

IMPORTANT SCOPE NOTE: `report.json`'s internal schema is NOT published by
Microsoft -- it's the same undocumented format Power BI Desktop itself
writes, reverse-engineered by the community (pbi-tools and others). This
builder produces a structurally valid, minimal version of it covering
five common visual types. It is a migration SCAFFOLD to open and refine
in Power BI Desktop, not a guarantee of visual/pixel parity with the
source Tableau dashboard -- anything outside the covered visual types
(maps, Gantt, dual-axis combos, custom Tableau viz extensions, etc.)
comes through as a table visual instead, flagged in coverage="partial" or
"unsupported" on the corresponding VisualMapping.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

# Tableau mark type -> nearest Power BI visualType identifier.
_MARK_TYPE_TO_VISUAL = {
    "Bar": "clusteredColumnChart",
    "Line": "lineChart",
    "Area": "areaChart",
    "Circle": "scatterChart",
    "Square": "clusteredColumnChart",
    "Pie": "pieChart",
    "Text": "card",
    "Automatic": "tableEx",
    "Shape": "scatterChart",
    "Gantt Bar": "unsupported",
    "Polygon": "unsupported",
    "Map": "unsupported",
    "Density": "unsupported",
}

_DEFAULT_PAGE_WIDTH = 1280
_DEFAULT_PAGE_HEIGHT = 720


def _short_id() -> str:
    return uuid.uuid4().hex[:20]


def _resolve_field_table(
    field_name: str, fields_index: dict[str, dict[str, Any]], hub_table: str
) -> tuple[str, str, bool]:
    """Looks up which table + column a Tableau internal field name maps
    to, and whether it's a measure (aggregated) or a plain column.
    fields_index is keyed by the Tableau internal field name (not
    caption) and built once per worksheet-mapping pass by the caller."""

    info = fields_index.get(field_name)
    if info is None:
        return hub_table, field_name, False
    return info["table"], info["column"], info["is_measure"]


def _visual_query_select(table: str, column: str, is_measure: bool, alias: str) -> dict[str, Any]:
    if is_measure:
        return {
            "Measure": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column},
            "Name": alias,
        }
    return {
        "Column": {"Expression": {"SourceRef": {"Source": "t"}}, "Property": column},
        "Name": alias,
    }


def _build_visual_config(
    visual_type: str,
    table: str,
    category_fields: list[tuple[str, str, bool]],
    value_fields: list[tuple[str, str, bool]],
) -> dict[str, Any]:
    """category_fields/value_fields are (table, column, is_measure) tuples."""

    select = []
    projections: dict[str, list[dict[str, str]]] = {}

    projection_key_for_role = {
        "category": "Category" if visual_type != "card" else "Values",
        "value": "Y" if visual_type in ("clusteredColumnChart", "lineChart", "areaChart") else "Values",
    }

    for i, (tbl, col, is_measure) in enumerate(category_fields):
        alias = f"{tbl}.{col}"
        select.append(_visual_query_select(tbl, col, is_measure, alias))
        projections.setdefault(projection_key_for_role["category"], []).append({"queryRef": alias})

    for i, (tbl, col, is_measure) in enumerate(value_fields):
        alias = f"{tbl}.{col}"
        select.append(_visual_query_select(tbl, col, is_measure, alias))
        projections.setdefault(projection_key_for_role["value"], []).append({"queryRef": alias})

    return {
        "name": _short_id(),
        "singleVisual": {
            "visualType": visual_type,
            "projections": projections,
            "prototypeQuery": {
                "Version": 2,
                "From": [{"Name": "t", "Entity": table, "Type": 0}],
                "Select": select,
            },
            "columnProperties": {},
        },
    }


def build_report_json(
    workbook_bundle: dict[str, Any], hub_table: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns (report_json_dict, visual_mappings) where visual_mappings
    matches the VisualMapping schema shape for the API response."""

    fields = workbook_bundle.get("fields", {})
    reports = workbook_bundle.get("reports", {})
    components = workbook_bundle.get("components", {})

    # Build a lookup from Tableau internal field name -> (table, column,
    # is_measure) so worksheet field_used lists (raw internal names) can
    # be resolved to real DAX-queryable columns/measures. Reuses the same
    # table-attribution convention as tmsl_builder.
    from app.services.migration.tmsl_builder import _attribute_field_to_table

    data_model_tables = {t.get("name", "") for t in workbook_bundle.get("data_model", {}).get("tables", [])}

    fields_index: dict[str, dict[str, Any]] = {}
    for dim in fields.get("dimensions", []):
        table, column = _attribute_field_to_table(dim.get("name", ""), hub_table, data_model_tables)
        fields_index[dim.get("name", "")] = {"table": table, "column": column, "is_measure": False}

    for calc in fields.get("calculated_fields", []):
        # Calculated fields were deployed as DAX measures (see
        # tmsl_builder.build_measures) under their caption/display name,
        # not their internal Tableau name -- reference them by that.
        table, _ = _attribute_field_to_table(calc.get("name", ""), hub_table, data_model_tables)
        display_name = calc.get("caption") or calc.get("name", "")
        fields_index[calc.get("name", "")] = {"table": table, "column": display_name, "is_measure": True}

    for measure in fields.get("measures", []):
        if measure.get("is_calculated"):
            continue  # already covered by calculated_fields above
        table, column = _attribute_field_to_table(measure.get("name", ""), hub_table, data_model_tables)
        fields_index[measure.get("name", "")] = {"table": table, "column": column, "is_measure": False}

    worksheet_lookup = {w["name"]: w for w in components.get("worksheets", [])}
    dashboards = components.get("dashboards", [])

    sections = []
    visual_mappings: list[dict[str, Any]] = []

    # Every dashboard becomes one report page. Standalone worksheets that
    # aren't on any dashboard get their own single-visual page too, so
    # nothing discovered is silently dropped from the migration.
    dashboard_worksheet_names = {w for d in dashboards for w in d.get("worksheets", [])}
    standalone_worksheets = [
        w for w in components.get("worksheets", []) if w["name"] not in dashboard_worksheet_names
    ]

    def _map_worksheet_to_visual(worksheet: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        mark_type = worksheet.get("mark_type", "Automatic")
        visual_type = _MARK_TYPE_TO_VISUAL.get(mark_type, "tableEx")

        resolved_fields = []
        unmapped = []
        for field_name in worksheet.get("fields_used", []):
            info = fields_index.get(field_name)
            if info is None:
                unmapped.append(field_name)
                continue
            resolved_fields.append((info["table"], info["column"], info["is_measure"], field_name))

        category_fields = [(t, c, m) for t, c, m, _ in resolved_fields if not m]
        value_fields = [(t, c, m) for t, c, m, _ in resolved_fields if m]

        coverage = "unsupported" if visual_type == "unsupported" else ("full" if not unmapped else "partial")
        if visual_type == "unsupported":
            visual_type = "tableEx"  # still render something rather than nothing

        table_for_query = (category_fields[0][0] if category_fields else (value_fields[0][0] if value_fields else hub_table))

        visual_config = _build_visual_config(visual_type, table_for_query, category_fields, value_fields)

        mapping = {
            "tableau_worksheet": worksheet["name"],
            "power_bi_visual_type": visual_type,
            "fields_mapped": [f"{t}.{c}" for t, c, _ in category_fields + value_fields],
            "fields_unmapped": unmapped,
            "coverage": coverage,
            "notes": (
                f"Tableau mark type '{mark_type}' has no direct Power BI equivalent; rendered as a table."
                if mark_type in ("Gantt Bar", "Polygon", "Map", "Density")
                else ""
            ),
        }
        return visual_config, mapping

    def _visual_container(visual_config: dict[str, Any], x: int, y: int, width: int, height: int) -> dict[str, Any]:
        return {
            "x": x,
            "y": y,
            "z": 0,
            "width": width,
            "height": height,
            "config": json.dumps(
                {
                    "name": visual_config["name"],
                    "layouts": [{"id": 0, "position": {"x": x, "y": y, "z": 0, "width": width, "height": height}}],
                    "singleVisual": visual_config["singleVisual"],
                }
            ),
            "filters": "[]",
        }

    ordinal = 0
    for dashboard in dashboards:
        page_id = _short_id()
        worksheet_names = dashboard.get("worksheets", [])

        visual_containers = []
        # Simple grid layout: 2 visuals per row, evenly sized to fit the
        # default page canvas. This reproduces roughly what was on the
        # dashboard, not Tableau's exact floating-layout positions (which
        # aren't captured by our TWB parser and would need dashboard zone
        # geometry to reproduce precisely).
        cols = 2
        cell_w = _DEFAULT_PAGE_WIDTH // cols
        cell_h = 240

        for idx, ws_name in enumerate(worksheet_names):
            worksheet = worksheet_lookup.get(ws_name)
            if worksheet is None:
                continue
            visual_config, mapping = _map_worksheet_to_visual(worksheet)
            visual_mappings.append(mapping)

            row, col = divmod(idx, cols)
            x, y = col * cell_w, row * cell_h
            visual_containers.append(_visual_container(visual_config, x, y, cell_w - 10, cell_h - 10))

        sections.append(
            {
                "name": page_id,
                "displayName": dashboard.get("name", f"Page {ordinal + 1}"),
                "ordinal": ordinal,
                "visualContainers": visual_containers,
                "filters": "[]",
                "config": json.dumps({}),
            }
        )
        ordinal += 1

    for worksheet in standalone_worksheets:
        page_id = _short_id()
        visual_config, mapping = _map_worksheet_to_visual(worksheet)
        visual_mappings.append(mapping)

        sections.append(
            {
                "name": page_id,
                "displayName": worksheet["name"],
                "ordinal": ordinal,
                "visualContainers": [
                    _visual_container(visual_config, 0, 0, _DEFAULT_PAGE_WIDTH - 20, _DEFAULT_PAGE_HEIGHT - 20)
                ],
                "filters": "[]",
                "config": json.dumps({}),
            }
        )
        ordinal += 1

    report_json = {
        "config": json.dumps({"version": "5.43", "themeCollection": {}}),
        "layoutOptimization": 0,
        "resourcePackages": [],
        "sections": sections,
    }

    return report_json, visual_mappings
