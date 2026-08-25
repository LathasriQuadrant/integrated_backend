"""
Field metadata discovery: dimensions, measures, calculated fields,
formulas and data types -- sourced entirely from the parsed TWB/TWBX XML,
which is the only source that exposes calculation formulas verbatim.
"""

from __future__ import annotations

from typing import Any


def discover_field_metadata(twb_data: dict[str, Any] | None) -> dict[str, Any]:
    dimensions: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    calculated_fields: list[dict[str, Any]] = []
    formulas: list[dict[str, Any]] = []
    data_types: list[dict[str, Any]] = []

    for ds in (twb_data or {}).get("datasources", []):
        for column in ds.get("columns", []):
            entry = {"datasource": ds["name"], **column}
            if column.get("role") == "dimension":
                dimensions.append(entry)
            elif column.get("role") == "measure":
                measures.append(entry)

        for calc in ds.get("calculated_fields", []):
            calculated_fields.append({"datasource": ds["name"], **calc})

        for formula in ds.get("formulas", []):
            formulas.append({"datasource": ds["name"], **formula})

        for dt in ds.get("data_types", []):
            data_types.append({"datasource": ds["name"], **dt})

    return {
        "dimensions": dimensions,
        "measures": measures,
        "calculated_fields": calculated_fields,
        "formulas": formulas,
        "data_types": data_types,
    }
