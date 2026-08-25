"""
Least-Used Reports analyzer.

For every workbook, sends its usage metadata to OpenAI and gets back a
0-100 popularity score plus a High/Medium/Low classification.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.prompts import USAGE_ANALYSIS_SYSTEM_PROMPT


async def analyze_usage(
    client: OpenAIAnalysisClient, workbook_bundles: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    results = []

    for bundle in workbook_bundles:
        wb_meta = bundle["workbook_metadata"]
        usage_payload = {
            "workbook_name": wb_meta.get("name", ""),
            "usage": bundle.get("usage", {}),
        }

        response = await client.complete_json(
            system_prompt=USAGE_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=json.dumps(usage_payload, default=str),
        )

        results.append(
            {
                "workbook_id": wb_meta.get("id", ""),
                "workbook_name": wb_meta.get("name", ""),
                "popularity_score": int(response.get("popularity_score", 0)),
                "usage_classification": response.get("usage_classification", "Low"),
                "rationale": response.get("rationale", ""),
            }
        )

    return results
