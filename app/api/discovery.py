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

def _normalize_mark_name(mark: str) -> str:
    m = (mark or "").strip().lower()
    mapping = {
        "bar": "Bar",
        "line": "Line",
        "area": "Area",
        "text": "Text",
        "circle": "Circle",
        "square": "Square",
        "pie": "Pie",
        "ganttbar": "Gantt",
        "polygon": "Map",
        "map": "Map",
        "shape": "Shape",
    }
    return mapping.get(m, (mark or "").strip() or "Automatic")


def _build_field_index(wb: dict) -> dict[str, dict]:
    """caption/name → {role, data_type} from discovery fields (dynamic, not sheet titles)."""
    index: dict[str, dict] = {}
    fields = wb.get("fields") or {}

    def add(entry: dict, default_role: str) -> None:
        role = (entry.get("role") or default_role or "").lower()
        dtype = (entry.get("data_type") or entry.get("datatype") or "").lower()
        for key in (entry.get("caption"), entry.get("name")):
            if not key:
                continue
            k = str(key).strip().lower()
            if k and k not in index:
                index[k] = {"role": role, "data_type": dtype}

    for d in fields.get("dimensions") or []:
        add(d, "dimension")
    for m in fields.get("measures") or []:
        add(m, "measure")
    for c in fields.get("calculated_fields") or []:
        add(c, c.get("role") or "measure")

    return index


def classify_visual_type_from_worksheet(
    w: dict,
    field_index: dict[str, dict] | None = None,
) -> str:
    """
    Dynamic classification:
      1) Trust non-Automatic Tableau mark_type
      2) Else infer from field roles / data types used on the sheet
      3) Else Bar (safest default for Automatic — not Line)
    No sheet-name keyword lists.
    """
    mark = (w.get("mark_type") or "").strip()
    if mark and mark.lower() not in ("automatic", "auto", ""):
        return _normalize_mark_name(mark)

    field_index = field_index or {}
    used = w.get("fields_used") or []

    n_dim = 0
    n_meas = 0
    n_date = 0
    n_geo = 0

    for raw in used:
        info = field_index.get(str(raw).strip().lower())
        if not info:
            # Unknown field: don't guess from its name
            continue
        role = info.get("role") or ""
        dtype = info.get("data_type") or ""

        if role == "measure":
            n_meas += 1
        else:
            n_dim += 1

        if dtype in ("date", "datetime", "localdate", "localdatetime"):
            n_date += 1
        if dtype in ("latitude", "longitude") or "geo" in dtype:
            n_geo += 1

    # Structural patterns only
    if n_geo >= 1:
        return "Map"

    # Measures only, no dimensions → KPI / card-style
    if n_meas >= 1 and n_dim == 0:
        return "Text"

    # Two+ measures, no dimensions → often scatter; still ambiguous → Bar safer than Line
    if n_meas >= 2 and n_dim == 0:
        return "Circle"

    # Dimension(s) + measure(s): could be bar or line.
    # Without shelves we cannot know continuous date on Columns vs discrete on Rows.
    # Prefer Bar so horizontal/vertical bars are not mislabeled as Line.
    if n_dim >= 1 and n_meas >= 1:
        return "Bar"

    if n_dim >= 1 and n_meas == 0:
        return "Text"  # crosstab / labels

    return "Bar"


def _build_visuals_payload(metadata: dict[str, Any]) -> dict[str, Any]:
    workbooks_out: list[dict[str, Any]] = []

    for wb in metadata.get("workbooks", []):
        meta = wb.get("workbook_metadata") or {}
        components = wb.get("components") or {}
        reports = wb.get("reports") or {}
        field_index = _build_field_index(wb)

        ws_by_name = {
            (w.get("name") or ""): w
            for w in (components.get("worksheets") or [])
            if w.get("name")
        }

        dashboards_src = components.get("dashboards") or reports.get("dashboards") or []
        dashboards_out: list[dict[str, Any]] = []
        used_ws: set[str] = set()

        for d in dashboards_src:
            d_name = d.get("name") or ""
            sheet_names = d.get("worksheets") or d.get("worksheets_contained") or []
            visuals: list[dict[str, Any]] = []
            for sheet_name in sheet_names:
                used_ws.add(sheet_name)
                w = ws_by_name.get(sheet_name, {"name": sheet_name})
                mark_type = w.get("mark_type") or "Automatic"
                visuals.append(
                    {
                        "worksheet_name": sheet_name,
                        "mark_type": mark_type,
                        "visual_type": classify_visual_type_from_worksheet(w, field_index),
                        "fields_used": w.get("fields_used") or [],
                        "filters": w.get("filters") or [],
                        "datasources": w.get("datasources") or [],
                    }
                )
            dashboards_out.append({"name": d_name, "visuals": visuals})

        orphans = []
        for name, w in ws_by_name.items():
            if name in used_ws:
                continue
            mark_type = w.get("mark_type") or "Automatic"
            orphans.append(
                {
                    "worksheet_name": name,
                    "mark_type": mark_type,
                    "visual_type": classify_visual_type_from_worksheet(w, field_index),
                    "fields_used": w.get("fields_used") or [],
                    "filters": w.get("filters") or [],
                    "datasources": w.get("datasources") or [],
                }
            )

        workbooks_out.append(
            {
                "workbook_id": meta.get("id", ""),
                "workbook_name": meta.get("name", ""),
                "dashboards": dashboards_out,
                "orphan_worksheets": orphans,
            }
        )

    return {"workbooks": workbooks_out}


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

def _attach_visuals_to_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Add workbook['visuals'] for each workbook (dashboards + orphan sheets)."""
    for wb in metadata.get("workbooks", []):
        components = wb.get("components") or {}
        reports = wb.get("reports") or {}
        field_index = _build_field_index(wb)

        ws_by_name = {
            (w.get("name") or ""): w
            for w in (components.get("worksheets") or [])
            if w.get("name")
        }

        dashboards_src = components.get("dashboards") or reports.get("dashboards") or []
        dashboards_out: list[dict[str, Any]] = []
        used_ws: set[str] = set()

        for d in dashboards_src:
            sheet_names = d.get("worksheets") or d.get("worksheets_contained") or []
            visuals: list[dict[str, Any]] = []
            for sheet_name in sheet_names:
                used_ws.add(sheet_name)
                w = ws_by_name.get(sheet_name, {"name": sheet_name})
                mark_type = w.get("mark_type") or "Automatic"
                visuals.append(
                    {
                        "worksheet_name": sheet_name,
                        "mark_type": mark_type,
                        "visual_type": classify_visual_type_from_worksheet(w, field_index),
                        "fields_used": w.get("fields_used") or [],
                        "filters": w.get("filters") or [],
                        "datasources": w.get("datasources") or [],
                    }
                )
            dashboards_out.append(
                {
                    "name": d.get("name") or "",
                    "visuals": visuals,
                }
            )

        orphans: list[dict[str, Any]] = []
        for name, w in ws_by_name.items():
            if name in used_ws:
                continue
            mark_type = w.get("mark_type") or "Automatic"
            orphans.append(
                {
                    "worksheet_name": name,
                    "mark_type": mark_type,
                    "visual_type": classify_visual_type_from_worksheet(w, field_index),
                    "fields_used": w.get("fields_used") or [],
                    "filters": w.get("filters") or [],
                    "datasources": w.get("datasources") or [],
                }
            )

        wb["visuals"] = {
            "dashboards": dashboards_out,
            "orphan_worksheets": orphans,
        }

    return metadata


@router.post("")
async def discover_all(request: DiscoveryRequest) -> dict[str, Any]:
    """Full discovery including per-workbook dashboard visuals."""
    metadata = await _run_discovery_for_request(request)
    return _attach_visuals_to_metadata(metadata)


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

@router.post("/visuals")
async def discover_visuals(request: DiscoveryRequest) -> dict[str, Any]:
    metadata = await _run_discovery_for_request(request)
    return _build_visuals_payload(metadata)
