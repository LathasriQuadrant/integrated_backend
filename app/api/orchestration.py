"""
POST /analyze

End-to-end orchestration: authenticate -> discovery -> normalization ->
AI analysis -> response. Each of the five AI analyzers can be toggled
off via the request body if only a subset is needed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.auth.session import create_session_for_request
from app.models.schemas import FullAnalyzeRequest
from app.services.ai.complexity_analyzer import analyze_complexity
from app.services.ai.datamodel_analyzer import analyze_shared_data_model
from app.services.ai.kpi_analyzer import analyze_kpis
from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.unused_asset_analyzer import analyze_unused_components
from app.services.ai.usage_analyzer import analyze_usage
from app.services.discovery.normalizer import run_discovery

router = APIRouter(tags=["Orchestration"])


@router.post("/analyze")
async def analyze(request: FullAnalyzeRequest) -> dict[str, Any]:
    session = create_session_for_request(request)

    try:
        metadata = await run_discovery(session, request.workbook_ids, request.include_twbx_parsing)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Discovery failed: {exc}") from exc
    finally:
        session.close()

    workbooks = metadata["workbooks"]
    response: dict[str, Any] = {"metadata": metadata}

    needs_ai = any(
        [
            request.run_usage_analysis,
            request.run_kpi_analysis,
            request.run_datamodel_analysis,
            request.run_unused_asset_analysis,
            request.run_complexity_analysis,
        ]
    )

    if needs_ai:
        ai_client = OpenAIAnalysisClient()
        if not ai_client.is_configured:
            raise HTTPException(
                status_code=500,
                detail="OPENAI_API_KEY is not configured. Set it in the environment/.env file.",
            )

        try:
            if request.run_usage_analysis:
                response["usage_analysis"] = await analyze_usage(ai_client, workbooks)

            if request.run_kpi_analysis:
                response["kpi_analysis"] = await analyze_kpis(ai_client, workbooks)

            if request.run_datamodel_analysis:
                response["datamodel_analysis"] = await analyze_shared_data_model(ai_client, workbooks)

            if request.run_unused_asset_analysis:
                response["unused_asset_analysis"] = await analyze_unused_components(ai_client, workbooks)

            if request.run_complexity_analysis:
                response["complexity_analysis"] = await analyze_complexity(ai_client, workbooks)

        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return response
