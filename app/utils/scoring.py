"""
Deterministic feature extraction used as input to the AI complexity
analyzer. Counting is deterministic (pulled straight from discovered
metadata); the actual weighted scoring/classification is delegated to
the OpenAI-backed analyzer per the project's scoring model.
"""

from __future__ import annotations

from typing import Any

COMPLEXITY_WEIGHTS = {
    "data_sources": 10,
    "calculated_fields": 15,
    "lod_expressions": 15,
    "table_calculations": 15,
    "relationships": 10,
    "parameters": 10,
    "filters": 5,
    "custom_sql": 20,
    "workbook_size": 15,
}

# Raw-count -> "this counts as maximally complex" ceilings, used to turn each
# raw count into a 0-1 severity fraction before applying its weight. These
# are calibrated against what's typically considered a large/complex
# enterprise Tableau workbook; counts at or above the ceiling saturate at
# severity 1.0 rather than scoring unbounded.
COMPLEXITY_CEILINGS = {
    "data_sources": 5,
    "calculated_fields": 40,
    "lod_expressions": 10,
    "table_calculations": 10,
    "relationships": 15,
    "parameters": 10,
    "filters": 20,
    "custom_sql": 5,
    "workbook_size_mb": 100,
}


def extract_complexity_features(bundle: dict[str, Any]) -> dict[str, Any]:
    data_model = bundle.get("data_model", {})
    fields = bundle.get("fields", {})
    components = bundle.get("components", {})

    calculated_fields = fields.get("calculated_fields", [])
    lod_count = sum(1 for c in calculated_fields if c.get("classification") == "lod_expression")
    table_calc_count = sum(1 for c in calculated_fields if c.get("classification") == "table_calculation")

    return {
        "data_sources": len(data_model.get("datasources", [])),
        "calculated_fields": len(calculated_fields),
        "lod_expressions": lod_count,
        "table_calculations": table_calc_count,
        "relationships": len(data_model.get("relationships", [])),
        "parameters": len(components.get("parameters", [])),
        "filters": len(components.get("filters", [])),
        "custom_sql": len(data_model.get("custom_sql", [])),
        "workbook_size_mb": bundle.get("workbook_metadata", {}).get("size_mb", 0),
    }


def classify_score(score: float) -> str:
    if score <= 30:
        return "Low"
    if score <= 70:
        return "Medium"
    return "High"


def compute_complexity_score(features: dict[str, Any]) -> dict[str, Any]:
    """Deterministically compute the weighted complexity score and its
    per-factor breakdown from raw counts, per the project's fixed scoring
    model. This is intentionally NOT delegated to the LLM: asking a model
    to both normalize counts into severities AND sum nine weighted terms
    produces an answer that isn't reliably self-consistent (the same
    inputs can yield a different score/breakdown on different calls, and
    the returned breakdown may not even sum to the returned score). Doing
    the arithmetic in Python guarantees the score is reproducible and that
    factor_breakdown always sums exactly to complexity_score.
    """

    severities: dict[str, float] = {}
    weighted: dict[str, float] = {}

    for factor, weight in COMPLEXITY_WEIGHTS.items():
        raw_key = "workbook_size_mb" if factor == "workbook_size" else factor
        raw_value = float(features.get(raw_key, 0) or 0)
        ceiling = COMPLEXITY_CEILINGS.get(raw_key, 1) or 1

        severity = max(0.0, min(1.0, raw_value / ceiling))
        severities[factor] = round(severity, 3)
        weighted[factor] = round(severity * weight, 2)

    total_score = round(sum(weighted.values()))
    total_score = max(0, min(100, total_score))

    return {
        "complexity_score": total_score,
        "complexity_classification": classify_score(total_score),
        "factor_breakdown": weighted,
        "severity_breakdown": severities,
    }