"""
Discovery endpoints (Phase 1).

Every endpoint is self-contained and stateless: it signs in to Tableau,
runs discovery, returns JSON, and signs out. Nothing is persisted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from app.auth.session import create_session_for_request
from app.models.schemas import DiscoveryRequest
from app.services.discovery.normalizer import run_discovery

router = APIRouter(prefix="/discovery", tags=["Discovery"])


async def _run_discovery_for_request(request: DiscoveryRequest) -> dict[str, Any]:
    session = create_session_for_request(request)
    try:
        return await run_discovery(session, request.workbook_ids, request.include_twbx_parsing)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Discovery failed: {exc}") from exc
    finally:
        session.close()


def _strip(metadata: dict[str, Any], key: str) -> dict[str, Any]:
    """Return {"workbook_name": ..., key: bundle[key]} for every workbook."""
    return {
        "workbooks": [
            {
                "workbook_id": wb["workbook_metadata"].get("id", ""),
                "workbook_name": wb["workbook_metadata"].get("name", ""),
                key: wb[key],
            }
            for wb in metadata["workbooks"]
        ]
    }


@router.post("")
async def discover_all(request: DiscoveryRequest) -> dict[str, Any]:
    """Full discovery: returns the complete normalized metadata object,
    covering every phase-1 facet for every requested (or all) workbooks."""
    return await _run_discovery_for_request(request)


@router.post("/workbooks")
async def discover_workbooks(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "workbook_metadata")


@router.post("/reports")
async def discover_reports(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "reports")


@router.post("/datasources")
async def discover_datasources(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "data_model")


@router.post("/fields")
async def discover_fields(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "fields")


@router.post("/kpis")
async def discover_kpis_endpoint(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "kpis")


@router.post("/lineage")
async def discover_lineage(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "dependencies")


@router.post("/mappings")
async def discover_mappings(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _strip(metadata, "mappings")
