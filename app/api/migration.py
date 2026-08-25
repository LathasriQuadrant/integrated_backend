"""
Migration endpoints (Phase 3): deploy a discovered Tableau workbook's
data model and reports to a Fabric workspace as a Power BI semantic
model + report.

Every endpoint accepts Fabric/Azure AD credentials directly in the
request body -- consistent with the stateless pattern used for Tableau
elsewhere in this app. Nothing is persisted between requests.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.migration_schemas import (
    FullMigrationRequest,
    FullMigrationResponse,
    ReportMigrationRequest,
    ReportMigrationResponse,
    SemanticModelMigrationRequest,
    SemanticModelMigrationResponse,
)
from app.services.migration.migration_orchestrator import (
    build_validation_summary,
    migrate_report,
    migrate_semantic_model,
)

router = APIRouter(prefix="/migration", tags=["Migration"])


@router.post("/semantic-model", response_model=SemanticModelMigrationResponse)
async def migration_semantic_model(request: SemanticModelMigrationRequest) -> SemanticModelMigrationResponse:
    try:
        result = await migrate_semantic_model(
            fabric_credentials=request.fabric,
            workspace_id=request.target.workspace_id,
            semantic_model_name=request.target.semantic_model_name,
            workbook_bundle=request.metadata,
            translate_calculated_fields=request.translate_calculated_fields,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Semantic model migration failed: {exc}") from exc

    return SemanticModelMigrationResponse(**{k: v for k, v in result.items() if k != "hub_table"})


@router.post("/report", response_model=ReportMigrationResponse)
async def migration_report(request: ReportMigrationRequest) -> ReportMigrationResponse:
    hub_table = request.metadata.get("data_model", {}).get("tables", [{}])[0].get("name", "")

    try:
        result = await migrate_report(
            fabric_credentials=request.fabric,
            workspace_id=request.target.workspace_id,
            report_name=request.target.report_name or f"{request.target.semantic_model_name} Report",
            semantic_model_id=request.semantic_model_id,
            workbook_bundle=request.metadata,
            hub_table=hub_table,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Report migration failed: {exc}") from exc

    return ReportMigrationResponse(**result)


@router.post("", response_model=FullMigrationResponse)
async def migration_full(request: FullMigrationRequest) -> FullMigrationResponse:
    """End-to-end: deploy the semantic model, translate calculated fields
    to DAX, then (if requested) deploy a bound report -- returning a
    reconciliation summary for manual validation against the source
    Tableau workbook."""

    try:
        semantic_model_result = await migrate_semantic_model(
            fabric_credentials=request.fabric,
            workspace_id=request.target.workspace_id,
            semantic_model_name=request.target.semantic_model_name,
            workbook_bundle=request.metadata,
            translate_calculated_fields=request.translate_calculated_fields,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Semantic model migration failed: {exc}") from exc

    report_result = None
    if request.deploy_report:
        try:
            report_result = await migrate_report(
                fabric_credentials=request.fabric,
                workspace_id=request.target.workspace_id,
                report_name=request.target.report_name or f"{request.target.semantic_model_name} Report",
                semantic_model_id=semantic_model_result["semantic_model_id"],
                workbook_bundle=request.metadata,
                hub_table=semantic_model_result["hub_table"],
            )
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Report migration failed: {exc}") from exc

    validation = build_validation_summary(request.metadata, semantic_model_result, report_result)

    return FullMigrationResponse(
        semantic_model=SemanticModelMigrationResponse(
            **{k: v for k, v in semantic_model_result.items() if k != "hub_table"}
        ),
        report=ReportMigrationResponse(**report_result) if report_result else None,
        validation=validation,
    )
