"""
Async Tableau REST API client.

Covers the REST endpoints needed for discovery: workbooks, views,
revisions, usage statistics, subscriptions, and permissions. All calls
are async (httpx) so discovery across many workbooks can be parallelized.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.auth.session import TableauSession
from app.config import get_settings


class TableauRestClient:
    def __init__(self, session: TableauSession):
        self._session = session
        self._settings = get_settings()
        self._base = f"{self._settings.TABLEAU_SERVER}/api/{self._settings.API_VERSION}"

    async def _get(self, client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict[str, Any]:
        url = f"{self._base}/sites/{self._session.site_id}{path}"
        resp = await client.get(url, headers=self._session.auth_headers, params=params)
        resp.raise_for_status()
        return resp.json()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._settings.REQUEST_TIMEOUT_SECONDS)

    # ------------------------------------------------------------------
    # Workbooks
    # ------------------------------------------------------------------

    async def list_workbooks(self) -> list[dict[str, Any]]:
        workbooks: list[dict[str, Any]] = []
        page_number = 1
        page_size = 100

        async with self._client() as client:
            while True:
                data = await self._get(
                    client,
                    "/workbooks",
                    params={"pageSize": page_size, "pageNumber": page_number},
                )
                pagination = data.get("pagination", {})
                batch = data.get("workbooks", {}).get("workbook", [])
                workbooks.extend(batch)

                total = int(pagination.get("totalAvailable", 0))
                if page_number * page_size >= total or not batch:
                    break
                page_number += 1

        return workbooks

    async def get_workbook(self, workbook_id: str) -> dict[str, Any]:
        async with self._client() as client:
            data = await self._get(client, f"/workbooks/{workbook_id}")
            return data.get("workbook", {})

    async def get_workbook_revisions(self, workbook_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            data = await self._get(client, f"/workbooks/{workbook_id}/revisions")
            revisions = data.get("revisions", {}).get("revision", [])
            return revisions if isinstance(revisions, list) else [revisions]

    async def get_workbook_connections(self, workbook_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            data = await self._get(client, f"/workbooks/{workbook_id}/connections")
            conns = data.get("connections", {}).get("connection", [])
            return conns if isinstance(conns, list) else [conns]

    async def download_workbook_file(self, workbook_id: str) -> bytes:
        url = f"{self._base}/sites/{self._session.site_id}/workbooks/{workbook_id}/content"
        async with self._client() as client:
            resp = await client.get(url, headers=self._session.auth_headers)
            resp.raise_for_status()
            return resp.content

    # ------------------------------------------------------------------
    # Views / Usage
    # ------------------------------------------------------------------

    async def get_workbook_views(self, workbook_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            data = await self._get(
                client, f"/workbooks/{workbook_id}/views", params={"includeUsageStatistics": "true"}
            )
            views = data.get("views", {}).get("view", [])
            return views if isinstance(views, list) else [views]

    async def get_view_permissions(self, view_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            data = await self._get(client, f"/views/{view_id}/permissions")
            grants = data.get("permissions", {}).get("granteeCapabilities", [])
            return grants if isinstance(grants, list) else [grants]

    async def get_workbook_permissions(self, workbook_id: str) -> list[dict[str, Any]]:
        async with self._client() as client:
            data = await self._get(client, f"/workbooks/{workbook_id}/permissions")
            grants = data.get("permissions", {}).get("granteeCapabilities", [])
            return grants if isinstance(grants, list) else [grants]

    async def get_subscriptions(self) -> list[dict[str, Any]]:
        async with self._client() as client:
            data = await self._get(client, "/subscriptions")
            subs = data.get("subscriptions", {}).get("subscription", [])
            return subs if isinstance(subs, list) else [subs]

    # ------------------------------------------------------------------
    # Data sources
    # ------------------------------------------------------------------

    async def list_datasources(self) -> list[dict[str, Any]]:
        datasources: list[dict[str, Any]] = []
        page_number = 1
        page_size = 100

        async with self._client() as client:
            while True:
                data = await self._get(
                    client,
                    "/datasources",
                    params={"pageSize": page_size, "pageNumber": page_number},
                )
                pagination = data.get("pagination", {})
                batch = data.get("datasources", {}).get("datasource", [])
                datasources.extend(batch)

                total = int(pagination.get("totalAvailable", 0))
                if page_number * page_size >= total or not batch:
                    break
                page_number += 1

        return datasources
