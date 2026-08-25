"""
Pydantic request/response schemas for Phase 3: Migration to Power BI (Fabric).

Mirrors the stateless-credentials pattern used for Tableau: Fabric/Azure AD
credentials are supplied per-request and never persisted. A service
principal (client-credentials OAuth2 flow) is the supported auth path,
since this backend runs unattended rather than as an interactive user.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class FabricCredentials(BaseModel):
    """Azure AD app registration (service principal) used to acquire a
    Fabric API token via the OAuth2 client-credentials flow. The service
    principal must be added as a member/contributor of the target
    workspace in Fabric for the calls below to succeed."""

    tenant_id: str
    client_id: str
    client_secret: str


class MigrationTarget(BaseModel):
    workspace_id: str = Field(description="Target Fabric workspace GUID.")
    semantic_model_name: str = Field(description="Display name for the new/updated semantic model.")
    report_name: Optional[str] = Field(
        default=None,
        description="Display name for the new/updated report. Defaults to '<semantic_model_name> Report'.",
    )


class SemanticModelMigrationRequest(BaseModel):
    fabric: FabricCredentials
    target: MigrationTarget
    metadata: dict[str, Any] = Field(
        description="Normalized metadata for ONE workbook (a single entry from /discovery's "
        "metadata.workbooks list) -- the workbook selected for migration."
    )
    translate_calculated_fields: bool = Field(
        default=True,
        description="Whether to AI-translate Tableau calculated fields into DAX measures. "
        "If false, calculated fields are skipped and only plain columns are migrated.",
    )


class ReportMigrationRequest(BaseModel):
    fabric: FabricCredentials
    target: MigrationTarget
    semantic_model_id: str = Field(description="ID of an already-deployed semantic model to bind the report to.")
    metadata: dict[str, Any] = Field(description="Normalized metadata for the same workbook used to deploy the semantic model.")
    field_name_to_measure_name: dict[str, str] = Field(
        default_factory=dict,
        description="Mapping from Tableau internal calculated-field name (e.g. 'Calculation_00143...') "
        "to the DAX measure name it was deployed as, so visuals reference the right measure. "
        "Returned by the semantic-model migration step.",
    )


class FullMigrationRequest(BaseModel):
    fabric: FabricCredentials
    target: MigrationTarget
    metadata: dict[str, Any] = Field(description="Normalized metadata for ONE workbook to migrate end-to-end.")
    translate_calculated_fields: bool = True
    deploy_report: bool = True


class MeasureTranslation(BaseModel):
    tableau_field_name: str
    dax_measure_name: str
    tableau_formula: str
    dax_expression: str
    confidence: str = Field(description="'high' | 'medium' | 'low' -- see needs_review for why")
    needs_review: bool = False
    review_reason: str = ""


class SemanticModelMigrationResponse(BaseModel):
    semantic_model_id: str
    semantic_model_name: str
    tables_deployed: list[str] = Field(default_factory=list)
    relationships_deployed: int = 0
    relationships_needing_review: list[dict[str, Any]] = Field(
        default_factory=list, description="Relationships whose cardinality/direction was inferred, not certain."
    )
    measures: list[MeasureTranslation] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class VisualMapping(BaseModel):
    tableau_worksheet: str
    power_bi_visual_type: str
    fields_mapped: list[str] = Field(default_factory=list)
    fields_unmapped: list[str] = Field(default_factory=list)
    coverage: str = Field(description="'full' | 'partial' | 'unsupported'")
    notes: str = ""


class ReportMigrationResponse(BaseModel):
    report_id: str
    report_name: str
    pages_deployed: list[str] = Field(default_factory=list)
    visual_mappings: list[VisualMapping] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MigrationValidationSummary(BaseModel):
    source_workbook_name: str
    tables_source_count: int
    tables_deployed_count: int
    measures_source_count: int
    measures_deployed_count: int
    measures_needing_review_count: int
    dashboards_source_count: int
    pages_deployed_count: int
    worksheets_source_count: int
    visuals_deployed_count: int
    visuals_unsupported: list[str] = Field(default_factory=list)
    overall_status: str = Field(description="'ready_for_review' | 'partial' | 'failed'")


class FullMigrationResponse(BaseModel):
    semantic_model: SemanticModelMigrationResponse
    report: Optional[ReportMigrationResponse] = None
    validation: MigrationValidationSummary
