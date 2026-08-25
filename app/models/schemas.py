"""
Pydantic request/response schemas shared across the API surface.

Everything is kept in-memory: these models are the contract between
the discovery layer, the AI analysis layer and the FastAPI routes.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

class SigninRequest(BaseModel):
    username: str
    password: str
    site_content_url: str = Field(default="", description="Tableau site content URL (blank for Default)")


class SigninResponse(BaseModel):
    token: str
    site_id: str
    site_content_url: str


class DiscoveryRequest(BaseModel):
    """Every discovery/analysis endpoint accepts credentials directly, OR
    an `api_token` already issued by the legacy POST /tableau/signin
    endpoint (so a user who's already signed in through the existing
    frontend flow doesn't have to authenticate to Tableau a second time).

    The app is otherwise stateless: credentials/tokens are only ever
    held in memory for the duration of a single request.
    """

    username: Optional[str] = None
    password: Optional[str] = None
    api_token: Optional[str] = Field(
        default=None,
        description="Alternative to username/password: an api_token previously "
        "returned by POST /tableau/signin. When set, discovery reuses that "
        "existing Tableau session instead of signing in again.",
    )
    site_content_url: str = ""
    workbook_ids: Optional[list[str]] = Field(
        default=None,
        description="Optional list of workbook LUIDs to scope discovery to. "
        "If omitted, all workbooks on the site are discovered.",
    )
    include_twbx_parsing: bool = Field(
        default=True,
        description="Whether to download and parse .twb/.twbx files for deep metadata.",
    )

    @model_validator(mode="after")
    def _require_credentials_or_token(self) -> "DiscoveryRequest":
        has_credentials = bool(self.username) and bool(self.password)
        has_token = bool(self.api_token)
        if not has_credentials and not has_token:
            raise ValueError(
                "Provide either username+password or api_token (from POST /tableau/signin)."
            )
        return self


class AnalysisRequest(BaseModel):
    """Used by AI-analysis-only endpoints when the caller already has
    normalized metadata (e.g. from a prior /discovery call) and wants to
    skip re-running discovery."""

    metadata: dict[str, Any] = Field(
        description="Normalized metadata object, matching the /discovery response shape."
    )


class FullAnalyzeRequest(DiscoveryRequest):
    """Input for the end-to-end orchestration endpoint."""

    run_usage_analysis: bool = True
    run_kpi_analysis: bool = True
    run_datamodel_analysis: bool = True
    run_unused_asset_analysis: bool = True
    run_complexity_analysis: bool = True


# --------------------------------------------------------------------------
# Discovery: Workbook metadata
# --------------------------------------------------------------------------

class WorkbookMetadata(BaseModel):
    id: str = ""
    name: str = ""
    description: str = ""
    owner: str = ""
    project: str = ""
    created_at: str = ""
    updated_at: str = ""
    published_at: str = ""
    revisions: list[dict[str, Any]] = Field(default_factory=list)


class ReportAssets(BaseModel):
    workbooks: list[dict[str, Any]] = Field(default_factory=list)
    dashboards: list[dict[str, Any]] = Field(default_factory=list)
    worksheets: list[dict[str, Any]] = Field(default_factory=list)


class UsageMetadata(BaseModel):
    view_counts: list[dict[str, Any]] = Field(default_factory=list)
    view_statistics: list[dict[str, Any]] = Field(default_factory=list)
    user_activity: list[dict[str, Any]] = Field(default_factory=list)
    subscriptions: list[dict[str, Any]] = Field(default_factory=list)
    permissions: list[dict[str, Any]] = Field(default_factory=list)


class DataModelMetadata(BaseModel):
    datasources: list[dict[str, Any]] = Field(default_factory=list)
    databases: list[dict[str, Any]] = Field(default_factory=list)
    schemas: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    joins: list[dict[str, Any]] = Field(default_factory=list)
    connections: list[dict[str, Any]] = Field(default_factory=list)
    custom_sql: list[dict[str, Any]] = Field(default_factory=list)


class FieldMetadata(BaseModel):
    dimensions: list[dict[str, Any]] = Field(default_factory=list)
    measures: list[dict[str, Any]] = Field(default_factory=list)
    calculated_fields: list[dict[str, Any]] = Field(default_factory=list)
    formulas: list[dict[str, Any]] = Field(default_factory=list)
    data_types: list[dict[str, Any]] = Field(default_factory=list)


class KpiMetadata(BaseModel):
    name: str = ""
    formula: str = ""
    aggregation: str = ""
    dependencies: list[str] = Field(default_factory=list)


class DependencyMetadata(BaseModel):
    upstream: list[dict[str, Any]] = Field(default_factory=list)
    downstream: list[dict[str, Any]] = Field(default_factory=list)
    workbook_dependencies: list[dict[str, Any]] = Field(default_factory=list)
    shared_datasources: list[dict[str, Any]] = Field(default_factory=list)


class ComponentMetadata(BaseModel):
    dashboards: list[dict[str, Any]] = Field(default_factory=list)
    worksheets: list[dict[str, Any]] = Field(default_factory=list)
    filters: list[dict[str, Any]] = Field(default_factory=list)
    parameters: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)


class MappingMetrics(BaseModel):
    reports_per_datasource: dict[str, int] = Field(default_factory=dict)
    datasources_per_dashboard: dict[str, int] = Field(default_factory=dict)
    shared_datasources: int = 0
    shared_tables: int = 0


class MappingMetadata(BaseModel):
    datasource_to_reports: dict[str, list[str]] = Field(default_factory=dict)
    dashboard_to_datasources: dict[str, list[str]] = Field(default_factory=dict)
    mapping_metrics: MappingMetrics = Field(default_factory=MappingMetrics)


class WorkbookBundle(BaseModel):
    """All normalized metadata for a single workbook."""

    workbook_metadata: WorkbookMetadata = Field(default_factory=WorkbookMetadata)
    reports: ReportAssets = Field(default_factory=ReportAssets)
    usage: UsageMetadata = Field(default_factory=UsageMetadata)
    data_model: DataModelMetadata = Field(default_factory=DataModelMetadata)
    fields: FieldMetadata = Field(default_factory=FieldMetadata)
    kpis: list[KpiMetadata] = Field(default_factory=list)
    dependencies: DependencyMetadata = Field(default_factory=DependencyMetadata)
    components: ComponentMetadata = Field(default_factory=ComponentMetadata)
    mappings: MappingMetadata = Field(default_factory=MappingMetadata)


class NormalizedMetadata(BaseModel):
    workbooks: list[WorkbookBundle] = Field(default_factory=list)


# --------------------------------------------------------------------------
# AI Analysis outputs
# --------------------------------------------------------------------------

class UsageAnalysisResult(BaseModel):
    workbook_id: str
    workbook_name: str
    popularity_score: int
    usage_classification: str
    rationale: str = ""


class KpiIntelligenceResult(BaseModel):
    duplicate_kpis: list[dict[str, Any]] = Field(default_factory=list)
    similar_kpis: list[dict[str, Any]] = Field(default_factory=list)
    kpi_clusters: list[dict[str, Any]] = Field(default_factory=list)


class SharedDataModelResult(BaseModel):
    shared_datasources: list[dict[str, Any]] = Field(default_factory=list)
    shared_tables: list[dict[str, Any]] = Field(default_factory=list)
    recommended_semantic_models: list[dict[str, Any]] = Field(default_factory=list)


class UnusedComponentResult(BaseModel):
    unused_worksheets: list[dict[str, Any]] = Field(default_factory=list)
    unused_calculated_fields: list[dict[str, Any]] = Field(default_factory=list)
    unused_filters: list[dict[str, Any]] = Field(default_factory=list)
    unused_parameters: list[dict[str, Any]] = Field(default_factory=list)
    unused_datasources: list[dict[str, Any]] = Field(default_factory=list)


class ComplexityAnalysisResult(BaseModel):
    workbook_id: str
    workbook_name: str
    complexity_score: int
    complexity_classification: str
    factor_breakdown: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""


class FullAnalysisResponse(BaseModel):
    metadata: NormalizedMetadata
    usage_analysis: list[UsageAnalysisResult] = Field(default_factory=list)
    kpi_analysis: Optional[KpiIntelligenceResult] = None
    datamodel_analysis: Optional[SharedDataModelResult] = None
    unused_asset_analysis: Optional[UnusedComponentResult] = None
    complexity_analysis: list[ComplexityAnalysisResult] = Field(default_factory=list)