"""
AI Analysis endpoints (Phase 2). 

Each endpoint accepts EITHER:
  * an AnalysisRequest with pre-computed `metadata` (e.g. the output of a
    prior /discovery call), to skip re-running discovery, OR
  * nothing else -- for full end-to-end runs use POST /analyze instead
    (see app/api/orchestration.py).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.schemas import AnalysisRequest
from app.services.ai.complexity_analyzer import analyze_complexity
from app.services.ai.datamodel_analyzer import analyze_shared_data_model
from app.services.ai.kpi_analyzer import analyze_kpis
from app.services.ai.openai_client import OpenAIAnalysisClient
from app.services.ai.unused_asset_analyzer import analyze_unused_components
from app.services.ai.usage_analyzer import analyze_usage

router = APIRouter(prefix="/analysis", tags=["AI Analysis"])


def _client() -> OpenAIAnalysisClient:
    client = OpenAIAnalysisClient()
    if not client.is_configured:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured. Set it in the environment/.env file.",
        )
    return client


def _workbooks(request: AnalysisRequest) -> list[dict[str, Any]]:
    workbooks = request.metadata.get("workbooks")
    if not workbooks:
        raise HTTPException(
            status_code=400,
            detail="metadata.workbooks is required (pass the output of a /discovery call).",
        )
    return workbooks


@router.post("/usage")
async def analysis_usage(request: AnalysisRequest) -> dict[str, Any]:
    client = _client()
    try:
        results = await analyze_usage(client, _workbooks(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"usage_analysis": results}


@router.post("/kpis")
async def analysis_kpis(request: AnalysisRequest) -> dict[str, Any]:
    client = _client()
    try:
        result = await analyze_kpis(client, _workbooks(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result


@router.post("/data-model")
async def analysis_data_model(request: AnalysisRequest) -> dict[str, Any]:
    client = _client()
    try:
        result = await analyze_shared_data_model(client, _workbooks(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result


@router.post("/unused-assets")
async def analysis_unused_assets(request: AnalysisRequest) -> dict[str, Any]:
    client = _client()
    try:
        result = await analyze_unused_components(client, _workbooks(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return result


@router.post("/complexity")
async def analysis_complexity(request: AnalysisRequest) -> dict[str, Any]:
    client = _client()
    try:
        results = await analyze_complexity(client, _workbooks(request))
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"complexity_analysis": results}
