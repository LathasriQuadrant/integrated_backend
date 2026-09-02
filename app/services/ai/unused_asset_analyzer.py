 
# """
# Unused Component analyzer.
 
# For every workbook, sends its full component inventory plus reference
# data (which fields/datasources worksheets actually use) to OpenAI to
# flag likely-unused assets ahead of migration.
# """
 
# from __future__ import annotations
 
# import json
# from typing import Any
 
# from app.services.ai.openai_client import OpenAIAnalysisClient
# from app.services.ai.prompts import UNUSED_COMPONENT_SYSTEM_PROMPT
# from app.utils.naming import build_datasource_name_lookup, build_field_name_lookup, resolve_name
 
 
# async def analyze_unused_components(
#     client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
# ) -> dict[str, Any]:
#     aggregate = {
#         "unused_worksheets": [],
#         "unused_calculated_fields": [],
#         "unused_filters": [],
#         "unused_parameters": [],
#         "unused_datasources": [],
#     }
 
#     for bundle in workbook_bundles:
#         workbook_name = bundle["workbook_metadata"]["name"]
 
#         field_lookup = build_field_name_lookup(bundle)
#         ds_lookup = build_datasource_name_lookup(bundle)
 
#         # Filters reference fields by internal name (e.g.
#         # "Calculation_0014397469077547"); translate before sending so
#         # the model reasons over readable names rather than echoing
#         # opaque ids straight through.
#         readable_filters = [
#             {**f, "column": resolve_name(f.get("column", ""), field_lookup)}
#             for f in bundle.get("components", {}).get("filters", [])
#         ]
 
#         payload = {
#             "workbook_name": workbook_name,
#             "worksheets": bundle.get("components", {}).get("worksheets", []),
#             "dashboards": bundle.get("components", {}).get("dashboards", []),
#             "calculated_fields": bundle.get("fields", {}).get("calculated_fields", []),
#             "filters": readable_filters,
#             "parameters": bundle.get("components", {}).get("parameters", []),
#             "datasources": bundle.get("data_model", {}).get("datasources", []),
#         }
 
#         response = await client.complete_json(
#             system_prompt=UNUSED_COMPONENT_SYSTEM_PROMPT,
#             user_prompt=json.dumps(payload, default=str),
#         )
 
#         # Belt-and-suspenders: even though the payload above already used
#         # readable names, don't rely on the model preserving that --
#         # translate every returned "name" through the same lookups before
#         # it reaches the response, so an internal id can never surface in
#         # a UI even if the model echoes a raw field/datasource dict key.
#         name_resolvers = {
#             "unused_worksheets": lambda n: n,  # already real names at discovery time
#             "unused_calculated_fields": lambda n: resolve_name(n, field_lookup),
#             "unused_filters": lambda n: resolve_name(n, field_lookup),
#             "unused_parameters": lambda n: resolve_name(n, field_lookup),
#             "unused_datasources": lambda n: resolve_name(n, ds_lookup),
#         }
 
#         for key in aggregate:
#             resolver = name_resolvers[key]
#             for item in response.get(key, []):
#                 resolved_item = {**item, "name": resolver(item.get("name", ""))}
#                 aggregate[key].append({"workbook": workbook_name, **resolved_item})
 
#     return aggregate

from __future__ import annotations

import json
import re
from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import UNUSED_COMPONENT_SYSTEM_PROMPT
from app.utils.naming import build_datasource_name_lookup, build_field_name_lookup, resolve_name

_FIELD_REF_PATTERN = re.compile(r"\[([^\]]+)\]")


def _referenced_fields(formula: str) -> set[str]:
    """Extract bracketed field references from a Tableau formula."""
    return {m.group(1).strip() for m in _FIELD_REF_PATTERN.finditer(formula or "")}


def _calc_aliases(calc: dict[str, Any]) -> set[str]:
    """All names under which this calculated field may appear in formulas
    or worksheet field lists (internal name and caption)."""
    aliases: set[str] = set()
    name = (calc.get("name") or "").strip()
    caption = (calc.get("caption") or "").strip()
    if name:
        aliases.add(name)
    if caption:
        aliases.add(caption)
    return aliases


def _used_calculated_field_names(bundle: dict[str, Any]) -> set[str]:
    """Return every calculated-field name/caption that is in use, either
    directly on a worksheet/filter or transitively via another used
    calculated field's formula.

    Example: Availability is only referenced inside OEE's formula, and
    OEE is on a sheet → both Availability and OEE are considered used.
    """
    calcs = bundle.get("fields", {}).get("calculated_fields", []) or []

    # Map every alias → the set of field refs in its formula.
    depends_on: dict[str, set[str]] = {}
    all_aliases: set[str] = set()
    for calc in calcs:
        aliases = _calc_aliases(calc)
        all_aliases |= aliases
        refs = _referenced_fields(calc.get("formula", ""))
        for alias in aliases:
            depends_on[alias] = set(refs)

    # Seeds: fields actually placed on worksheets or used as filters.
    used: set[str] = set()
    for ws in bundle.get("components", {}).get("worksheets", []) or []:
        for f in ws.get("fields_used", []) or []:
            if f:
                used.add(str(f).strip())
        for f in ws.get("filters", []) or []:
            if f:
                used.add(str(f).strip())
    for f in bundle.get("components", {}).get("filters", []) or []:
        col = f.get("column", "") if isinstance(f, dict) else f
        if col:
            used.add(str(col).strip())

    # Transitive closure over calculated-field dependencies.
    # Also expand each used name to all of its aliases (name + caption).
    changed = True
    while changed:
        changed = False
        newly: set[str] = set()
        for alias in list(used):
            for dep in depends_on.get(alias, set()):
                if dep not in used and dep in all_aliases:
                    newly.add(dep)
            for calc in calcs:
                if alias in _calc_aliases(calc):
                    newly |= _calc_aliases(calc)
        before = len(used)
        used |= newly
        if len(used) > before:
            changed = True

    return used


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

        # Deterministic safety net: never flag a calculated field that is
        # used on a sheet OR referenced (transitively) by another used
        # calculated field (e.g. Availability used only inside OEE).
        used_calc_names = _used_calculated_field_names(bundle)

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
                raw_name = item.get("name", "")
                resolved_name = resolver(raw_name)
                if key == "unused_calculated_fields":
                    if resolved_name in used_calc_names or raw_name in used_calc_names:
                        continue
                resolved_item = {**item, "name": resolved_name}
                aggregate[key].append({"workbook": workbook_name, **resolved_item})

    return aggregate
