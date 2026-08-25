"""
Migration Complexity analyzer.

Extracts deterministic structural counts per workbook (data sources,
calculated fields, LOD expressions, table calcs, relationships,
parameters, filters, custom SQL, workbook size), computes the weighted
0-100 complexity score and Low/Medium/High classification IN PYTHON per
the project's fixed scoring model (see app.utils.scoring), and calls
OpenAI only to produce a human-readable rationale grounded in those
already-computed numbers.

The score is deliberately not delegated to the LLM: asking a model to
both normalize raw counts into severities and sum nine weighted terms
does not reliably reproduce the same score for the same inputs, and the
returned breakdown can fail to sum to the returned score (observed in
practice). Computing it in Python guarantees the score is deterministic
and that factor_breakdown always sums exactly to complexity_score.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import COMPLEXITY_RATIONALE_SYSTEM_PROMPT
from app.utils.scoring import compute_complexity_score, extract_complexity_features


async def analyze_complexity(
    client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    results = []

    for bundle in workbook_bundles:
        wb_meta = bundle["workbook_metadata"]
        features = extract_complexity_features(bundle)
        scored = compute_complexity_score(features)

        rationale = ""
        try:
            payload = {
                "workbook_name": wb_meta.get("name", ""),
                "raw_counts": features,
                "complexity_score": scored["complexity_score"],
                "complexity_classification": scored["complexity_classification"],
                "weighted_factor_breakdown": scored["factor_breakdown"],
            }
            response = await client.complete_json(
                system_prompt=COMPLEXITY_RATIONALE_SYSTEM_PROMPT,
                user_prompt=json.dumps(payload, default=str),
            )
            rationale = response.get("rationale", "")
        except RuntimeError:
            # A missing/failed OpenAI call shouldn't take down the whole
            # analyzer -- the deterministic score/classification/breakdown
            # are still fully valid without a narrative rationale.
            rationale = ""

        results.append(
            {
                "workbook_id": wb_meta.get("id", ""),
                "workbook_name": wb_meta.get("name", ""),
                "complexity_score": scored["complexity_score"],
                "complexity_classification": scored["complexity_classification"],
                "factor_breakdown": scored["factor_breakdown"],
                "rationale": rationale,
            }
        )

    return results