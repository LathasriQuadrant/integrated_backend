"""
Request-scoped Tableau session.

This app is stateless by default: a TableauSession is created at the
start of a request, used to authenticate all downstream REST / Metadata
API calls, and discarded (with a best-effort sign-out) at the end of
the request. Nothing here is ever written to disk or cached across
requests.

The one exception is when a caller reuses an `api_token` issued by the
legacy `/tableau/signin` endpoint (see `create_session_from_legacy_token`
below): in that case the underlying Tableau token is owned by the
legacy token store, not by this request, so `close()` must NOT sign it
out -- doing so would kill the session for every other `/tableau/*`
call still using that api_token.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from requests.exceptions import HTTPError, RequestException

from app.auth.legacy_token_store import get_legacy_auth
from app.auth.tableau_auth import signin_with_credentials, signout
from app.config import get_settings


@dataclass
class TableauSession:
    token: str
    site_id: str
    site_content_url: str
    # False when this session wraps a token borrowed from the legacy
    # /tableau/signin token store -- close() becomes a no-op so that
    # store's session stays alive for other requests.
    owns_token: bool = field(default=True)

    @property
    def auth_headers(self) -> dict[str, str]:
        return {
            "X-Tableau-Auth": self.token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def close(self) -> None:
        if self.owns_token:
            signout(self.token)


def create_session(username: str, password: str, site_content_url: str = "") -> TableauSession:
    settings = get_settings()

    if not settings.TABLEAU_SERVER:
        raise HTTPException(
            status_code=500,
            detail="TABLEAU_SERVER is not configured. Set it in the environment/.env file.",
        )

    try:
        token, site_id = signin_with_credentials(username, password, site_content_url)
    except HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 502
        raise HTTPException(
            status_code=401 if status in (401, 403) else 502,
            detail=f"Tableau authentication failed: {exc}",
        ) from exc
    except RequestException as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach Tableau Server: {exc}"
        ) from exc

    return TableauSession(token=token, site_id=site_id, site_content_url=site_content_url)


def create_session_from_legacy_token(api_token: str, site_content_url: str = "") -> TableauSession:
    """Build a TableauSession that reuses a Tableau auth token already
    issued via POST /tableau/signin, instead of signing in again.

    Lets the discovery/analysis/orchestration endpoints accept an
    `api_token` (from a user who already signed in through the existing
    frontend flow) as an alternative to username/password, without
    opening a second, independent Tableau login.
    """
    try:
        legacy_auth = get_legacy_auth(api_token)
    except KeyError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    return TableauSession(
        token=legacy_auth["auth_token"],
        site_id=legacy_auth["site_id"],
        site_content_url=site_content_url,
        owns_token=False,
    )


def create_session_for_request(request) -> TableauSession:
    """Dispatch helper for any request model with `username`/`password`/
    `api_token`/`site_content_url` fields (DiscoveryRequest, FullAnalyzeRequest):
    reuses a legacy api_token when present, otherwise signs in fresh."""
    if getattr(request, "api_token", None):
        return create_session_from_legacy_token(request.api_token, request.site_content_url)
    return create_session(request.username, request.password, request.site_content_url)
