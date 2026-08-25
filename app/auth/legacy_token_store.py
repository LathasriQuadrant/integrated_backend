"""
Shared in-memory token store for the legacy `/tableau/*` sign-in model.

The original `tableau_backend` app signs in once via POST /tableau/signin
and hands the frontend an opaque `api_token`, which it then reuses on
every subsequent `/tableau/*` call (fetch_data, download_workbook, ...).
That behavior is preserved unchanged here.

This module also lets the newer, stateless Pre-Migration AI Analysis
endpoints (`/discovery`, `/analyze`, ...) *optionally* reuse an
already-issued `api_token` instead of forcing the user to sign in to
Tableau a second time with username/password. See
`app.auth.session.create_session_from_legacy_token`.

Process-local, in-memory only -- identical persistence characteristics
to the original backend's `TOKEN_STORE` dict (lost on restart, not
shared across worker processes).
"""

from __future__ import annotations

TOKEN_STORE: dict[str, dict[str, str]] = {}


def get_legacy_auth(api_token: str) -> dict[str, str]:
    """Look up the Tableau auth_token/site_id for a legacy api_token.

    Raises KeyError if the api_token is invalid or expired -- callers
    are responsible for translating that into the appropriate HTTP error
    for their framework (matches the original backend's behavior of
    raising a RuntimeError with the same message).
    """
    if api_token not in TOKEN_STORE:
        raise KeyError("Invalid or expired api_token")
    return TOKEN_STORE[api_token]
