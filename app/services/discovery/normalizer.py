# """
# Discovery orchestrator.

# Runs every discovery sub-service for a set of workbooks (in parallel),
# then normalizes everything into the single JSON shape defined in
# app.models.schemas.NormalizedMetadata. This normalized object is the
# sole input to every AI analyzer in Phase 2.

# Nothing here touches a database -- all state lives in local Python
# objects for the duration of the request.
# """

# from __future__ import annotations

# import asyncio
# import logging
# from typing import Any

# from app.auth.session import TableauSession
# from app.services.discovery.component_service import discover_components
# from app.services.discovery.datamodel_service import discover_data_model
# from app.services.discovery.dependency_service import discover_dependencies
# from app.services.discovery.field_service import discover_field_metadata
# from app.services.discovery.kpi_service import discover_kpis
# from app.services.discovery.mapping_service import build_mappings, per_workbook_mapping
# from app.services.discovery.metadata_api_client import TableauMetadataApiClient
# from app.services.discovery.report_service import discover_report_assets
# from app.services.discovery.rest_client import TableauRestClient
# from app.services.discovery.twbx_parser import parse_workbook_file
# from app.services.discovery.usage_service import discover_usage_metadata
# from app.services.discovery.workbook_service import discover_workbook_metadata

# logger = logging.getLogger(__name__)


# async def _discover_single_workbook(
#     rest_client: TableauRestClient,
#     metadata_client: TableauMetadataApiClient,
#     workbook_id: str,
#     include_twbx_parsing: bool,
# ) -> dict[str, Any]:
#     workbook_metadata = await discover_workbook_metadata(rest_client, metadata_client, workbook_id)
#     workbook_graph = workbook_metadata.pop("_graph", {})
#     workbook_name = workbook_metadata["name"]

#     twb_data: dict[str, Any] | None = None
#     if include_twbx_parsing:
#         try:
#             file_bytes = await rest_client.download_workbook_file(workbook_id)
#             # Tableau's /content endpoint returns either a .twb or .twbx
#             # payload; we can't always trust the filename, so sniff the
#             # zip magic number to decide which parser to use.
#             filename = f"{workbook_id}.twbx" if file_bytes[:2] == b"PK" else f"{workbook_id}.twb"
#             twb_data = parse_workbook_file(filename, file_bytes)
#         except Exception as exc:  # noqa: BLE001 - discovery must be resilient per-workbook
#             logger.warning("TWB/TWBX parsing failed for workbook %s: %s", workbook_id, exc)
#             twb_data = None

#     reports = await discover_report_assets(rest_client, workbook_id, workbook_name, twb_data)

#     all_subscriptions = await rest_client.get_subscriptions()
#     usage = await discover_usage_metadata(rest_client, workbook_id, all_subscriptions)

#     data_model = await discover_data_model(rest_client, metadata_client, workbook_id, twb_data)
#     fields = discover_field_metadata(twb_data)
#     kpis = discover_kpis(fields)
#     dependencies = await discover_dependencies(metadata_client, workbook_graph)
#     components = discover_components(twb_data)

#     return {
#         "workbook_metadata": workbook_metadata,
#         "reports": reports,
#         "usage": usage,
#         "data_model": data_model,
#         "fields": fields,
#         "kpis": kpis,
#         "dependencies": dependencies,
#         "components": components,
#         "mappings": {},  # filled in after cross-workbook mapping is computed
#     }


# async def discover_workbook_ids(rest_client: TableauRestClient) -> list[str]:
#     workbooks = await rest_client.list_workbooks()
#     return [wb.get("id") for wb in workbooks if wb.get("id")]


# async def run_discovery(
#     session: TableauSession,
#     workbook_ids: list[str] | None,
#     include_twbx_parsing: bool = True,
# ) -> dict[str, Any]:
#     rest_client = TableauRestClient(session)
#     metadata_client = TableauMetadataApiClient(session)

#     target_ids = workbook_ids or await discover_workbook_ids(rest_client)

#     results = await asyncio.gather(
#         *[
#             _discover_single_workbook(rest_client, metadata_client, wb_id, include_twbx_parsing)
#             for wb_id in target_ids
#         ],
#         return_exceptions=True,
#     )

#     bundles: list[dict[str, Any]] = []
#     for wb_id, result in zip(target_ids, results):
#         if isinstance(result, Exception):
#             logger.warning("Discovery failed for workbook %s: %s", wb_id, result)
#             continue
#         bundles.append(result)

#     global_mappings = build_mappings(bundles)
#     for bundle in bundles:
#         bundle["mappings"] = per_workbook_mapping(bundle, global_mappings)

#     return {"workbooks": bundles}

"""
Discovery orchestrator.

Runs every discovery sub-service for a set of workbooks (in parallel),
then normalizes everything into the single JSON shape defined in
app.models.schemas.NormalizedMetadata. This normalized object is the
sole input to every AI analyzer in Phase 2.

Nothing here touches a database -- all state lives in local Python
objects for the duration of the request.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.auth.session import TableauSession
from app.services.discovery.component_service import discover_components
from app.services.discovery.datamodel_service import discover_data_model
from app.services.discovery.dependency_service import discover_dependencies
from app.services.discovery.field_service import discover_field_metadata
from app.services.discovery.kpi_service import discover_kpis
from app.services.discovery.mapping_service import build_mappings, per_workbook_mapping
from app.services.discovery.metadata_api_client import TableauMetadataApiClient
from app.services.discovery.report_service import discover_report_assets
from app.services.discovery.rest_client import TableauRestClient
from app.services.discovery.twbx_parser import parse_workbook_file
from app.services.discovery.usage_service import discover_usage_metadata
from app.services.discovery.workbook_service import discover_workbook_metadata

logger = logging.getLogger(__name__)


async def _discover_single_workbook(
    rest_client: TableauRestClient,
    metadata_client: TableauMetadataApiClient,
    workbook_id: str,
    include_twbx_parsing: bool,
) -> dict[str, Any]:
    workbook_metadata = await discover_workbook_metadata(rest_client, metadata_client, workbook_id)
    workbook_graph = workbook_metadata.pop("_graph", {})
    workbook_name = workbook_metadata["name"]

    twb_data: dict[str, Any] | None = None
    if include_twbx_parsing:
        try:
            file_bytes = await rest_client.download_workbook_file(workbook_id)
            # Tableau's /content endpoint returns either a .twb or .twbx
            # payload; we can't always trust the filename, so sniff the
            # zip magic number to decide which parser to use.
            filename = f"{workbook_id}.twbx" if file_bytes[:2] == b"PK" else f"{workbook_id}.twb"
            twb_data = parse_workbook_file(filename, file_bytes)
        except Exception as exc:  # noqa: BLE001 - discovery must be resilient per-workbook
            logger.warning("TWB/TWBX parsing failed for workbook %s: %s", workbook_id, exc)
            twb_data = None

    reports = await discover_report_assets(rest_client, workbook_id, workbook_name, twb_data)

    all_subscriptions = await rest_client.get_subscriptions()
    usage = await discover_usage_metadata(rest_client, workbook_id, all_subscriptions)

    data_model = await discover_data_model(rest_client, metadata_client, workbook_id, twb_data, workbook_graph)
    fields = discover_field_metadata(twb_data)
    kpis = discover_kpis(fields)
    dependencies = await discover_dependencies(metadata_client, workbook_graph)
    components = discover_components(twb_data)

    return {
        "workbook_metadata": workbook_metadata,
        "reports": reports,
        "usage": usage,
        "data_model": data_model,
        "fields": fields,
        "kpis": kpis,
        "dependencies": dependencies,
        "components": components,
        "mappings": {},  # filled in after cross-workbook mapping is computed
    }


async def discover_workbook_ids(rest_client: TableauRestClient) -> list[str]:
    workbooks = await rest_client.list_workbooks()
    return [wb.get("id") for wb in workbooks if wb.get("id")]


async def run_discovery(
    session: TableauSession,
    workbook_ids: list[str] | None,
    include_twbx_parsing: bool = True,
) -> dict[str, Any]:
    rest_client = TableauRestClient(session)
    metadata_client = TableauMetadataApiClient(session)

    target_ids = workbook_ids or await discover_workbook_ids(rest_client)

    results = await asyncio.gather(
        *[
            _discover_single_workbook(rest_client, metadata_client, wb_id, include_twbx_parsing)
            for wb_id in target_ids
        ],
        return_exceptions=True,
    )

    bundles: list[dict[str, Any]] = []
    for wb_id, result in zip(target_ids, results):
        if isinstance(result, Exception):
            logger.warning("Discovery failed for workbook %s: %s", wb_id, result)
            continue
        bundles.append(result)

    global_mappings = build_mappings(bundles)
    for bundle in bundles:
        bundle["mappings"] = per_workbook_mapping(bundle, global_mappings)

    return {"workbooks": bundles}
