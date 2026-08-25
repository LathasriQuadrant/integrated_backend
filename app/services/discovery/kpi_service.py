"""
KPI discovery: identifies candidate KPIs from measures and calculated
fields. A field is treated as a KPI candidate when it is a measure (or a
numeric calculated field) that carries an aggregation and is referenced
by other calculated fields (a signal that it represents a "headline"
business metric rather than an intermediate helper calc).
"""

from __future__ import annotations

import re
from typing import Any

FIELD_REF_PATTERN = re.compile(r"\[([^\]]+)\]")


def _referenced_fields(formula: str) -> list[str]:
    return list({m.group(1) for m in FIELD_REF_PATTERN.finditer(formula or "")})


def _is_internal_field(field: dict[str, Any]) -> bool:
    """Tableau's modern "Relationships" data model injects one pseudo-field
    per logical table (name like
    ``__tableau_internal_object_id__].[Dim_Date_15F66DF...``, data_type
    "table") to back the object-graph. These aren't real business fields
    and must never be treated as KPI candidates."""

    name = field.get("name", "")
    return field.get("data_type") == "table" or "__tableau_internal_object_id__" in name


def discover_kpis(fields: dict[str, Any]) -> list[dict[str, Any]]:
    kpis: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Build a dependency graph: field name -> [fields it depends on]
    dependency_map: dict[str, list[str]] = {}
    for calc in fields.get("calculated_fields", []):
        dependency_map[calc["name"]] = _referenced_fields(calc.get("formula", ""))

    # Which fields are referenced by at least one other calculated field.
    referenced_by_others: set[str] = set()
    for deps in dependency_map.values():
        referenced_by_others.update(deps)

    # Calculated fields (drawn straight from measures/dimensions with a
    # <calculation>) always carry the richest data -- formula, real
    # aggregation intent, dependencies -- so process them FIRST and let
    # `seen_names` suppress the duplicate, formula-less entry that would
    # otherwise also come through `measures` for the same field.
    for calc in fields.get("calculated_fields", []):
        if _is_internal_field(calc):
            continue

        name = calc["name"]
        has_aggregation = bool(re.search(r"(SUM|AVG|COUNT|MIN|MAX|MEDIAN)\s*\(", calc.get("formula", ""), re.I))
        is_headline = name in referenced_by_others or has_aggregation

        if not is_headline:
            continue

        display_name = calc.get("caption") or name
        if display_name in seen_names:
            continue
        seen_names.add(display_name)

        kpis.append(
            {
                "name": display_name,
                "formula": calc.get("formula", ""),
                "aggregation": "custom" if not has_aggregation else "aggregated",
                "dependencies": dependency_map.get(name, []),
                "source": "calculated_field",
                "datasource": calc.get("datasource", ""),
            }
        )

    # Plain (non-calculated) measures: real numeric columns with a default
    # aggregation set in Tableau, e.g. `scrap_qty` with default-aggregation
    # SUM. Calculated measures are excluded here since they were already
    # captured above with their formula.
    for measure in fields.get("measures", []):
        if _is_internal_field(measure) or measure.get("is_calculated"):
            continue

        name = measure["name"]
        display_name = measure.get("caption") or name
        if display_name in seen_names:
            continue
        seen_names.add(display_name)

        kpis.append(
            {
                "name": display_name,
                "formula": "",
                "aggregation": measure.get("default_aggregation", ""),
                "dependencies": [],
                "source": "measure",
                "datasource": measure.get("datasource", ""),
            }
        )

    return kpis