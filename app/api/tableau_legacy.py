"""
Legacy `/tableau/*` routes.

This is a direct FastAPI port of the original `tableau_backend` Flask
app's `main.py`. Request/response JSON shapes, status codes, and
behavior are preserved exactly so the existing frontend (TableauAuthModal,
Explorer) keeps working unmodified against this integrated backend.

Session model (unchanged): POST /tableau/signin signs in to Tableau once
and returns an opaque `api_token` that the frontend stores and reuses on
every subsequent `/tableau/*` call. Tokens live in the shared in-memory
`TOKEN_STORE` (see app.auth.legacy_token_store) for the life of the
process -- same persistence characteristics as the original backend.

That same TOKEN_STORE is also consulted by the newer Pre-Migration AI
Analysis endpoints (/discovery, /analyze, ...) when they're called with
an `api_token` instead of username/password, so a user who already
signed in here does not have to authenticate to Tableau a second time.
"""

from __future__ import annotations

import os
import uuid
from typing import Optional

import requests
from azure.storage.blob import BlobServiceClient, ContentSettings
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.auth.legacy_token_store import TOKEN_STORE, get_legacy_auth
from app.config import get_settings

router = APIRouter(prefix="/tableau", tags=["Tableau (legacy)"])

TIMEOUT = 30


# ================== REQUEST MODELS ==================

class SigninBody(BaseModel):
    username: str
    password: str
    site_content_url: str = ""


class TokenBody(BaseModel):
    api_token: str


class WorkbookBody(BaseModel):
    api_token: str
    workbook_id: str


class DownloadWorkbookBody(BaseModel):
    api_token: str
    workbook_id: str
    file_name: Optional[str] = None


class DownloadWorkbookDatasourcesBody(BaseModel):
    api_token: str
    workbook_id: str


# ================== HELPERS ==================

def _safe_request(method: str, url: str, headers: dict | None = None, json_body: dict | None = None,
                   stream: bool = False) -> requests.Response:
    r = requests.request(method, url, headers=headers, json=json_body, stream=stream, timeout=TIMEOUT)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"{r.status_code} - {r.text}")
    return r


def _get_auth(api_token: str) -> dict[str, str]:
    try:
        return get_legacy_auth(api_token)
    except KeyError as exc:
        raise RuntimeError(str(exc)) from exc


def _upload_to_azure(file_path: str, blob_name: str) -> str:
    settings = get_settings()
    if not settings.AZURE_CONNECTION_STRING or not settings.AZURE_CONTAINER_NAME:
        raise RuntimeError(
            "AZURE_CONNECTION_STRING / AZURE_CONTAINER_NAME are not configured."
        )
    blob_service = BlobServiceClient.from_connection_string(settings.AZURE_CONNECTION_STRING)
    blob_client = blob_service.get_blob_client(container=settings.AZURE_CONTAINER_NAME, blob=blob_name)
    with open(file_path, "rb") as f:
        blob_client.upload_blob(
            f, overwrite=True, content_settings=ContentSettings(content_type="application/octet-stream")
        )
    return blob_client.url


# ================== SIGN IN ==================

@router.post("/signin")
def signin(body: SigninBody):
    settings = get_settings()
    try:
        payload = {
            "credentials": {
                "name": body.username,
                "password": body.password,
                "site": {"contentUrl": body.site_content_url or ""},
            }
        }

        r = _safe_request(
            "POST",
            f"{settings.TABLEAU_SERVER}/api/{settings.API_VERSION}/auth/signin",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json_body=payload,
        )

        creds = r.json()["credentials"]
        api_token = str(uuid.uuid4())

        TOKEN_STORE[api_token] = {
            "auth_token": creds["token"],
            "site_id": creds["site"]["id"],
        }

        return {"api_token": api_token}

    except Exception as e:  # noqa: BLE001 -- match legacy behavior: any failure -> 401 JSON error
        return JSONResponse(status_code=401, content={"error": "Signin failed", "details": str(e)})


# ================== FETCH METADATA (FLAT JSON) ==================

@router.post("/fetch_data")
def fetch_data(body: TokenBody):
    settings = get_settings()
    try:
        auth = _get_auth(body.api_token)
        headers = {"X-Tableau-Auth": auth["auth_token"], "Accept": "application/json"}
        base = f"{settings.TABLEAU_SERVER}/api/{settings.API_VERSION}/sites/{auth['site_id']}"

        projects = _safe_request("GET", f"{base}/projects", headers).json()["projects"]["project"]
        workbooks = _safe_request("GET", f"{base}/workbooks", headers).json()["workbooks"]["workbook"]
        views = _safe_request("GET", f"{base}/views", headers).json()["views"]["view"]
        datasources = _safe_request("GET", f"{base}/datasources", headers).json()["datasources"]["datasource"]

        return {
            "projects": [
                {"id": p["id"], "name": p["name"], "parent_id": p.get("parentProjectId")}
                for p in projects
            ],
            "workbooks": [
                {"id": w["id"], "name": w["name"], "project_id": w.get("project", {}).get("id")}
                for w in workbooks
            ],
            "views": [
                {"id": v["id"], "name": v["name"], "workbook_id": v.get("workbook", {}).get("id")}
                for v in views
            ],
            "datasources": [
                {"id": d["id"], "name": d["name"], "project_id": d.get("project", {}).get("id")}
                for d in datasources
            ],
        }

    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(e)})


# ================== WORKBOOK -> DATASOURCES ==================

@router.post("/workbook_datasources")
def workbook_datasources(body: WorkbookBody):
    settings = get_settings()
    try:
        auth = _get_auth(body.api_token)
        headers = {"X-Tableau-Auth": auth["auth_token"], "Accept": "application/json"}

        url = (
            f"{settings.TABLEAU_SERVER}/api/{settings.API_VERSION}/sites/{auth['site_id']}"
            f"/workbooks/{body.workbook_id}/connections"
        )
        connections = _safe_request("GET", url, headers).json()["connections"]["connection"]

        datasources = []
        for c in connections:
            if c.get("datasource"):
                datasources.append({
                    "datasource_name": c["datasource"]["name"],
                    "datasource_id": c["datasource"]["id"],
                    "published": True,
                })

        return {"workbook_id": body.workbook_id, "datasources": datasources}

    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(e)})


# ================== TECHNICAL CONNECTION DETAILS ==================

@router.post("/get_connections")
def get_connections(body: WorkbookBody):
    settings = get_settings()
    try:
        auth = _get_auth(body.api_token)
        headers = {"X-Tableau-Auth": auth["auth_token"], "Accept": "application/json"}

        url = (
            f"{settings.TABLEAU_SERVER}/api/{settings.API_VERSION}/sites/{auth['site_id']}"
            f"/workbooks/{body.workbook_id}/connections"
        )
        connections = _safe_request("GET", url, headers).json()["connections"]["connection"]

        return {"workbook_id": body.workbook_id, "connections": connections}

    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(e)})


# ================== DOWNLOAD WORKBOOK (.twbx) ==================

@router.post("/download_workbook")
def download_workbook(body: DownloadWorkbookBody):
    settings = get_settings()
    try:
        auth = _get_auth(body.api_token)

        os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)
        filename = body.file_name or f"{body.workbook_id}.twbx"
        local_path = os.path.join(settings.DOWNLOAD_DIR, filename)

        url = (
            f"{settings.TABLEAU_SERVER}/api/{settings.API_VERSION}/sites/{auth['site_id']}"
            f"/workbooks/{body.workbook_id}/content"
        )
        r = _safe_request("GET", url, {"X-Tableau-Auth": auth["auth_token"]}, stream=True)

        with open(local_path, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)

        blob_url = _upload_to_azure(local_path, filename)
        os.remove(local_path)

        return {"blob_url": blob_url}

    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": str(e)})


# ================== DOWNLOAD WORKBOOK DATASOURCES (.tdsx) ==================

@router.post("/download_workbook_datasources")
def download_workbook_datasources(body: DownloadWorkbookDatasourcesBody):
    settings = get_settings()
    try:
        auth = _get_auth(body.api_token)
        headers = {"X-Tableau-Auth": auth["auth_token"], "Accept": "application/json"}
        base = f"{settings.TABLEAU_SERVER}/api/{settings.API_VERSION}/sites/{auth['site_id']}"

        os.makedirs(settings.DOWNLOAD_DIR, exist_ok=True)

        published = _safe_request("GET", f"{base}/datasources", headers).json()
        published_map = {ds["id"]: ds["name"] for ds in published["datasources"]["datasource"]}

        connections = _safe_request(
            "GET", f"{base}/workbooks/{body.workbook_id}/connections", headers
        ).json()["connections"]["connection"]

        uploaded, skipped = [], []

        for c in connections:
            ds = c.get("datasource")
            if not ds or ds["id"] not in published_map:
                skipped.append({
                    "datasource_name": ds["name"] if ds else None,
                    "reason": "Embedded datasource",
                })
                continue

            name = published_map[ds["id"]].replace(" ", "_") + ".tdsx"
            local_path = os.path.join(settings.DOWNLOAD_DIR, name)

            r = _safe_request(
                "GET", f"{base}/datasources/{ds['id']}/content",
                {"X-Tableau-Auth": auth["auth_token"]}, stream=True,
            )

            with open(local_path, "wb") as f:
                for chunk in r.iter_content(8192):
                    f.write(chunk)

            blob_url = _upload_to_azure(local_path, name)
            os.remove(local_path)

            uploaded.append({"datasource_name": published_map[ds["id"]], "blob_url": blob_url})

        return {"uploaded": uploaded, "skipped": skipped}

    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=400, content={"error": "Datasource download failed", "details": str(e)})
