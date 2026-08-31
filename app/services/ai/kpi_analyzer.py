# """
# KPI Intelligence analyzer.
 
# Collects every KPI candidate across all workbooks and sends them to
# OpenAI in a single call to detect duplicates, near-duplicates, and
# thematic clusters. Run globally (not per-workbook) since duplication is
# inherently a cross-workbook concern.
# """
 

# from __future__ import annotations
 
# import json
# from itertools import combinations
# from typing import Any
 
# from app.services.ai.openai_client import OpenAIAnalysisClient
# from app.services.ai.prompts import KPI_INTELLIGENCE_SYSTEM_PROMPT
# from app.utils.formula_equivalence import check_equivalence
# from app.utils.naming import build_datasource_name_lookup, resolve_name
 
 
# def _deterministic_duplicate_groups(kpis: list[dict[str, Any]]) -> list[dict[str, Any]]:
#     """Group KPIs whose formulas are provably the same calculation.
 
#     Runs check_equivalence for every candidate pair that has a formula
#     and shares at least one dependency field (a cheap prefilter -- two
#     formulas with zero fields in common can never be equal, so there's
#     no reason to pay for the randomized-evaluation check on those
#     pairs). Equivalence is transitive, so results are merged with a
#     union-find rather than only ever forming pairs.
#     """
#     formula_kpis = [k for k in kpis if (k.get("formula") or "").strip()]
 
#     parent = {k["name"]: k["name"] for k in formula_kpis}
 
#     def find(x: str) -> str:
#         while parent[x] != x:
#             parent[x] = parent[parent[x]]
#             x = parent[x]
#         return x
 
#     def union(a: str, b: str) -> None:
#         ra, rb = find(a), find(b)
#         if ra != rb:
#             parent[ra] = rb
 
#     for a, b in combinations(formula_kpis, 2):
#         deps_a = set(a.get("dependencies") or [])
#         deps_b = set(b.get("dependencies") or [])
#         if deps_a and deps_b and not (deps_a & deps_b):
#             continue
#         if check_equivalence(a["formula"], b["formula"]).equivalent:
#             union(a["name"], b["name"])
 
#     groups: dict[str, list[str]] = {}
#     for k in formula_kpis:
#         groups.setdefault(find(k["name"]), []).append(k["name"])
 
#     return [
#         {
#             "group_name": members[0],
#             "kpis": members,
#             "reason": (
#                 "Formulas are mathematically equivalent: confirmed by evaluating both "
#                 "against randomized and edge-case inputs, not by comparing formula text."
#             ),
#         }
#         for members in groups.values()
#         if len(members) > 1
#     ]
 
 
# async def analyze_kpis(
#     client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
# ) -> dict[str, Any]:
#     all_kpis = []
#     for bundle in workbook_bundles:
#         workbook_name = bundle["workbook_metadata"]["name"]
#         ds_lookup = build_datasource_name_lookup(bundle)
#         for kpi in bundle.get("kpis", []):
#             # kpi["name"] is already a caption (see
#             # app.services.discovery.kpi_service), but kpi["datasource"]
#             # is still the internal Tableau name -- translate it too so
#             # nothing opaque reaches the model or the response.
#             all_kpis.append(
#                 {
#                     **kpi,
#                     "datasource": resolve_name(kpi.get("datasource", ""), ds_lookup),
#                     "workbook": workbook_name,
#                 }
#             )
 
#     if not all_kpis:
#         return {"duplicate_kpis": [], "similar_kpis": [], "kpi_clusters": []}
 
#     deterministic_duplicates = _deterministic_duplicate_groups(all_kpis)
#     already_grouped = {name for g in deterministic_duplicates for name in g["kpis"]}
 
#     response = await client.complete_json(
#         system_prompt=KPI_INTELLIGENCE_SYSTEM_PROMPT,
#         user_prompt=json.dumps(
#             {
#                 "kpis": all_kpis,
#                 "already_confirmed_duplicate_groups": deterministic_duplicates,
#             },
#             default=str,
#         ),
#     )
 
#     # The deterministic check is authoritative for anything with a
#     # formula. Keep any LLM-proposed duplicate group only if it doesn't
#     # touch a KPI we've already grouped -- covers KPIs with no formula,
#     # which the LLM judges on name/metadata semantics instead.
#     llm_duplicates = [
#         g
#         for g in response.get("duplicate_kpis", [])
#         if not (set(g.get("kpis", [])) & already_grouped)
#     ]
 
#     return {
#         "duplicate_kpis": deterministic_duplicates + llm_duplicates,
#         "similar_kpis": response.get("similar_kpis", []),
#         "kpi_clusters": response.get("kpi_clusters", []),
#     }

from __future__ import annotations
 
import json
from itertools import combinations
from typing import Any
 
from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import KPI_INTELLIGENCE_SYSTEM_PROMPT
from app.utils.formula_equivalence import check_equivalence
from app.utils.naming import build_datasource_name_lookup, resolve_name
 
 
def _deterministic_duplicate_groups(kpis: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group KPIs whose formulas are provably the same calculation.
 
    Runs check_equivalence for every candidate pair that has a formula
    and shares at least one dependency field (a cheap prefilter -- two
    formulas with zero fields in common can never be equal, so there's
    no reason to pay for the randomized-evaluation check on those
    pairs). Equivalence is transitive, so results are merged with a
    union-find rather than only ever forming pairs.
    """
    formula_kpis = [k for k in kpis if (k.get("formula") or "").strip()]
 
    parent = {k["name"]: k["name"] for k in formula_kpis}
 
    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
 
    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
 
    for a, b in combinations(formula_kpis, 2):
        deps_a = set(a.get("dependencies") or [])
        deps_b = set(b.get("dependencies") or [])
        if deps_a and deps_b and not (deps_a & deps_b):
            continue
        if check_equivalence(a["formula"], b["formula"]).equivalent:
            union(a["name"], b["name"])
 
    groups: dict[str, list[str]] = {}
    for k in formula_kpis:
        groups.setdefault(find(k["name"]), []).append(k["name"])
 
    return [
        {
            "group_name": members[0],
            "kpis": members,
            "reason": (
                "Formulas are mathematically equivalent: confirmed by evaluating both "
                "against randomized and edge-case inputs, not by comparing formula text."
            ),
        }
        for members in groups.values()
        if len(members) > 1
    ]
 
 
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
 
    deterministic_duplicates = _deterministic_duplicate_groups(all_kpis)
    already_grouped = {name for g in deterministic_duplicates for name in g["kpis"]}
 
    response = await client.complete_json(
        system_prompt=KPI_INTELLIGENCE_SYSTEM_PROMPT,
        user_prompt=json.dumps(
            {
                "kpis": all_kpis,
                "already_confirmed_duplicate_groups": deterministic_duplicates,
            },
            default=str,
        ),
    )
 
    # The deterministic check is authoritative for anything with a
    # formula. Keep any LLM-proposed duplicate group only if it doesn't
    # touch a KPI we've already grouped -- covers KPIs with no formula,
    # which the LLM judges on name/metadata semantics instead.
    llm_duplicates = [
        g
        for g in response.get("duplicate_kpis", [])
        if not (set(g.get("kpis", [])) & already_grouped)
    ]
 
    # The LLM never re-derives membership for deterministic groups (see
    # prompt) -- it only judges which member is best to keep, returned
    # separately keyed by group_name since group/kpis membership for
    # those is already settled locally. Merge the recommendation back in
    # here so every duplicate group ends up with the same recommendation
    # fields regardless of whether it was found deterministically or by
    # the LLM.
    recommendation_by_group = {
        rec.get("group_name"): rec
        for rec in response.get("confirmed_duplicate_recommendations", [])
        if rec.get("group_name")
    }
    for group in deterministic_duplicates:
        rec = recommendation_by_group.get(group["group_name"])
        if rec:
            group["recommended_keep"] = rec.get("recommended_keep", "")
            group["recommended_remove"] = rec.get("recommended_remove", [])
            group["recommendation_rationale"] = rec.get("recommendation_rationale", "")
        else:
            # LLM response didn't cover this group (e.g. truncated) --
            # fall back rather than silently omitting the fields, so the
            # frontend can always rely on them being present.
            group.setdefault("recommended_keep", "")
            group.setdefault("recommended_remove", [])
            group.setdefault("recommendation_rationale", "")
 
    return {
        "duplicate_kpis": deterministic_duplicates + llm_duplicates,
        "similar_kpis": response.get("similar_kpis", []),
        "kpi_clusters": response.get("kpi_clusters", []),
    }
