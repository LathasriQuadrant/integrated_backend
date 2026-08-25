"""
AI-assisted Tableau calculated-field formula -> DAX measure translator.

Reuses the same OpenAI client used by the Phase-2 analyzers. Every
translation is returned with a confidence level and, for formulas that
are structurally risky to auto-translate (LOD expressions, table
calculations), an explicit needs_review flag -- these categories don't
have a mechanical 1:1 DAX equivalent (LOD -> CALCULATE with
FILTER/ALLEXCEPT depending on FIXED/INCLUDE/EXCLUDE semantics; table
calcs -> window functions or iterator patterns depending on the exact
partition/ordering), so a generated translation here is a strong starting
point, not a guarantee of semantic equivalence.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.ai.openai_client import OpenAIAnalysisClient

DAX_TRANSLATION_SYSTEM_PROMPT = """You are a BI migration engineer translating Tableau calculated-field \
formulas into Power BI DAX measures.

You will be given the field's Tableau formula, its data type, and its \
classification (standard | lod_expression | table_calculation).

Rules:
- Standard aggregations (SUM/AVG/COUNT/MIN/MAX of a column) translate \
  directly, e.g. SUM([produced_qty]) -> SUM('Table'[produced_qty]).
- IF/THEN/ELSE/END translates to DAX IF() or SWITCH().
- LOD expressions (FIXED/INCLUDE/EXCLUDE) need CALCULATE with FILTER/ \
  ALLEXCEPT/REMOVEFILTERS depending on which dimensions are fixed/ \
  included/excluded from the calculation's context. State your best \
  translation but mark confidence "low" or "medium" and needs_review \
  true, since exact context-transition semantics are easy to get subtly \
  wrong and must be checked against real data.
- Table calculations (RANK, RUNNING_SUM, WINDOW_*, LOOKUP, TOTAL, \
  PREVIOUS_VALUE) need DAX window functions or iterators (RANKX, \
  ISONORAFTER + variables, or index-based CALCULATE patterns) since \
  Tableau's partition/addressing model doesn't map 1:1 onto DAX's \
  filter-context model. Always mark these needs_review true with \
  confidence "low" or "medium" and explain the specific risk in \
  review_reason (e.g. "Tableau RANK partition/direction must be verified \
  against the actual visual's dimensions").
- Bracketed Tableau field references [field_name] become 'TableName'[field_name] \
  in DAX. Use the provided table_name for this.
- Never invent a translation you're not reasonably confident in -- if a \
  formula is too ambiguous to translate without the addressing/ \
  partitioning context of its Tableau visual, return your best-guess \
  structure but set confidence "low" and needs_review true with a clear \
  review_reason.

Respond with ONLY a JSON object of this exact shape, no prose, no markdown:
{
  "dax_expression": "<the DAX measure expression, without 'Measure = ' prefix>",
  "confidence": "high" | "medium" | "low",
  "needs_review": true | false,
  "review_reason": "<empty string if needs_review is false, else a specific reason>"
}
"""


async def translate_formula_to_dax(
    client: OpenAIAnalysisClient,
    tableau_formula: str,
    data_type: str,
    classification: str,
    table_name: str,
) -> dict[str, Any]:
    if not client.is_configured:
        return {
            "dax_expression": "",
            "confidence": "low",
            "needs_review": True,
            "review_reason": "OPENAI_API_KEY not configured -- formula was not translated.",
        }

    payload = {
        "tableau_formula": tableau_formula,
        "data_type": data_type,
        "classification": classification,
        "table_name": table_name,
    }

    try:
        response = await client.complete_json(
            system_prompt=DAX_TRANSLATION_SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, default=str),
        )
    except RuntimeError as exc:
        return {
            "dax_expression": "",
            "confidence": "low",
            "needs_review": True,
            "review_reason": f"Translation failed: {exc}",
        }

    return {
        "dax_expression": response.get("dax_expression", ""),
        "confidence": response.get("confidence", "low"),
        "needs_review": bool(response.get("needs_review", True)),
        "review_reason": response.get("review_reason", ""),
    }
