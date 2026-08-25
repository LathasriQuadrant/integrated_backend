"""
Fabric REST API client for deploying a semantic model (TMSL) and a report
(PBIR-Legacy) to a Fabric workspace, per Microsoft's documented
definition-based item APIs:
  https://learn.microsoft.com/rest/api/fabric/semanticmodel/items/create-semantic-model
  https://learn.microsoft.com/rest/api/fabric/report/items/create-report

Both are long-running operations: a 202 response carries a Location
header pointing at /v1/operations/{id}, which this client polls until
the deployment finishes.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx
from fastapi import HTTPException

from app.services.migration.fabric_auth import FABRIC_API_BASE

_POLL_INTERVAL_SECONDS = 3
_MAX_POLL_ATTEMPTS = 60  # 3 minutes


def _b64_json(obj: dict[str, Any]) -> str:
    return base64.b64encode(json.dumps(obj, indent=2).encode("utf-8")).decode("ascii")


def _b64_text(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


class FabricClient:
    def __init__(self, access_token: str):
        self._headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    async def _poll_lro(self, client: httpx.AsyncClient, operation_location: str) -> dict[str, Any]:
        for _ in range(_MAX_POLL_ATTEMPTS):
            resp = await client.get(operation_location, headers=self._headers)
            resp.raise_for_status()
            body = resp.json()
            status = body.get("status", "")

            if status == "Succeeded":
                result_resp = await client.get(f"{operation_location}/result", headers=self._headers)
                if result_resp.status_code == 200:
                    return result_resp.json()
                return body
            if status == "Failed":
                raise HTTPException(
                    status_code=502,
                    detail=f"Fabric deployment operation failed: {body.get('error', body)}",
                )

            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

        raise HTTPException(status_code=504, detail="Fabric deployment operation timed out after 3 minutes.")

    async def _create_item_with_definition(
        self,
        client: httpx.AsyncClient,
        workspace_id: str,
        item_path: str,
        display_name: str,
        description: str,
        parts: list[dict[str, str]],
    ) -> dict[str, Any]:
        url = f"{FABRIC_API_BASE}/workspaces/{workspace_id}/{item_path}"
        payload = {
            "displayName": display_name,
            "description": description[:256],
            "definition": {"parts": parts},
        }

        resp = await client.post(url, headers=self._headers, json=payload)

        if resp.status_code == 201:
            return resp.json()

        if resp.status_code == 202:
            operation_location = resp.headers.get("Location")
            if not operation_location:
                raise HTTPException(status_code=502, detail="Fabric returned 202 without an operation Location header.")
            result = await self._poll_lro(client, operation_location)
            return result

        raise HTTPException(
            status_code=resp.status_code if resp.status_code >= 400 else 502,
            detail=f"Fabric item creation failed: {resp.status_code} {resp.text}",
        )

    async def create_semantic_model(
        self, workspace_id: str, display_name: str, description: str, model_bim: dict[str, Any]
    ) -> str:
        """Deploys a TMSL (model.bim) semantic model definition. Returns
        the new semantic model's item id."""

        pbism = {"version": "4.0", "settings": {}}

        parts = [
            {"path": "model.bim", "payload": _b64_json(model_bim), "payloadType": "InlineBase64"},
            {"path": "definition.pbism", "payload": _b64_json(pbism), "payloadType": "InlineBase64"},
        ]

        async with httpx.AsyncClient(timeout=60) as client:
            result = await self._create_item_with_definition(
                client, workspace_id, "semanticModels", display_name, description, parts
            )

        item_id = result.get("id")
        if not item_id:
            raise HTTPException(status_code=502, detail=f"Fabric did not return a semantic model id: {result}")
        return item_id

    async def create_report(
        self,
        workspace_id: str,
        display_name: str,
        description: str,
        report_json: dict[str, Any],
        semantic_model_id: str,
    ) -> str:
        """Deploys a PBIR-Legacy report bound to an existing semantic
        model via definition.pbir's byConnection reference. Returns the
        new report's item id."""

        definition_pbir = {
            "version": "4.0",
            "datasetReference": {
                "byPath": None,
                "byConnection": {
                    "connectionString": None,
                    "pbiServiceModelId": None,
                    "pbiModelVirtualServerName": "sobe_wowvirtualserver",
                    "pbiModelDatabaseName": semantic_model_id,
                    "connectionType": "pbiServiceXmlaStyleLive",
                    "name": "EntityDataSource",
                },
            },
        }

        platform = {
            "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
            "metadata": {"type": "Report", "displayName": display_name},
            "config": {"version": "2.0", "logicalId": ""},
        }

        parts = [
            {"path": "report.json", "payload": _b64_json(report_json), "payloadType": "InlineBase64"},
            {"path": "definition.pbir", "payload": _b64_json(definition_pbir), "payloadType": "InlineBase64"},
            {"path": ".platform", "payload": _b64_json(platform), "payloadType": "InlineBase64"},
        ]

        async with httpx.AsyncClient(timeout=60) as client:
            result = await self._create_item_with_definition(
                client, workspace_id, "reports", display_name, description, parts
            )

        item_id = result.get("id")
        if not item_id:
            raise HTTPException(status_code=502, detail=f"Fabric did not return a report id: {result}")
        return item_id
