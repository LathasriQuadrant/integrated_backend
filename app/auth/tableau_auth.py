"""
Tableau Server / Tableau Cloud authentication.

Uses the exact username/password sign-in implementation specified for
this project. Credentials and the resulting token are never written to
disk; callers are responsible for holding the returned token only in
memory for the lifetime of a single request.
"""

import requests

from app.config import API_VERSION, TABLEAU_SERVER


def signin_with_credentials(
    username,
    password,
    site_content_url=""
):

    url = (
        f"{TABLEAU_SERVER}"
        f"/api/{API_VERSION}/auth/signin"
    )

    payload = {
        "credentials": {
            "name": username,
            "password": password,
            "site": {
                "contentUrl": site_content_url
            }
        }
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    response = requests.post(
        url,
        json=payload,
        headers=headers
    )

    response.raise_for_status()

    data = response.json()

    return (
        data["credentials"]["token"],
        data["credentials"]["site"]["id"]
    )


def signout(token: str) -> None:
    """Invalidate a Tableau auth token. Best-effort; failures are swallowed
    since sign-out is a cleanup step, not a critical path."""

    if not token:
        return

    url = f"{TABLEAU_SERVER}/api/{API_VERSION}/auth/signout"
    headers = {"X-Tableau-Auth": token, "Accept": "application/json"}

    try:
        requests.post(url, headers=headers, timeout=15)
    except requests.RequestException:
        pass
