"""
Azure AD (Entra ID) token acquisition for the Fabric REST API.

Uses the OAuth2 client-credentials flow with a service principal -- this
backend runs unattended, so a delegated user-login flow isn't
appropriate. The service principal must be granted at least Contributor
on the target Fabric workspace for the calls in fabric_client.py to
succeed (Create/Update Semantic Model requires SemanticModel.ReadWrite.All
or Item.ReadWrite.All).

Nothing here is cached across requests -- consistent with the rest of
this app, a fresh token is acquired per request and discarded.
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException

from app.models.migration_schemas import FabricCredentials

FABRIC_API_SCOPE = "https://api.fabric.microsoft.com/.default"
FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"


async def get_fabric_access_token(credentials: FabricCredentials) -> str:
    token_url = f"https://login.microsoftonline.com/{credentials.tenant_id}/oauth2/v2.0/token"

    payload = {
        "grant_type": "client_credentials",
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scope": FABRIC_API_SCOPE,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(token_url, data=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=401,
                detail=(
                    "Azure AD authentication failed for the Fabric API. Verify tenant_id, "
                    f"client_id, and client_secret. Details: {exc.response.text}"
                ),
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach Azure AD: {exc}") from exc

    token_data = resp.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise HTTPException(status_code=401, detail="Azure AD did not return an access token.")

    return access_token
