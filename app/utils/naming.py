"""
Resolves Tableau's internal identifiers (datasource names like
"federated.12p60851wjjcxx1brr7rf13bgtp0", calculated-field names like
"Calculation_0014397467750439") to their human-readable captions.
 
Tableau's TWB XML always carries both: an opaque internal `name` used to
wire up XML references, and a `caption` that's what the person actually
typed in the UI ("Fact_Production+ (Manufacturing_Analytics)", "Quality",
etc). Discovery keeps both since the internal name is sometimes needed to
resolve other XML references, but nothing user-facing -- especially AI
analyzer output meant for a UI -- should ever surface the internal name
when a caption exists.
"""
 
from __future__ import annotations
 
from typing import Any
 
 
def build_datasource_name_lookup(bundle: dict[str, Any]) -> dict[str, str]:
    """internal datasource name -> caption (falls back to the internal
    name itself if no caption was captured)."""
    lookup: dict[str, str] = {}
    for ds in bundle.get("data_model", {}).get("datasources", []):
        name = ds.get("name", "")
        if name:
            lookup[name] = ds.get("caption") or name
    return lookup
 
 
def build_field_name_lookup(bundle: dict[str, Any]) -> dict[str, str]:
    """internal field name (e.g. "Calculation_0014397467750439") ->
    caption (e.g. "Quality"), covering dimensions, measures, and
    calculated fields."""
    lookup: dict[str, str] = {}
    fields = bundle.get("fields", {})
    for bucket in ("dimensions", "measures", "calculated_fields"):
        for field in fields.get(bucket, []):
            name = field.get("name", "")
            if name:
                lookup[name] = field.get("caption") or name
    return lookup
 
 
def resolve_name(name: str, *lookups: dict[str, str]) -> str:
    """Looks up `name` in each lookup in order, returning the first
    caption found, or `name` unchanged if none of the lookups know it."""
    for lookup in lookups:
        if name in lookup:
            return lookup[name]
    return name