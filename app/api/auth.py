"""
POST /auth/signin

Authenticates against Tableau Server/Cloud and returns the auth token
and site id. The token is NOT stored server-side -- the app is
stateless, so callers must pass their username/password on every
subsequent discovery/analysis call (each of which performs its own
sign-in internally and signs out when done).
"""

from fastapi import APIRouter

from app.auth.session import create_session
from app.models.schemas import SigninRequest, SigninResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signin", response_model=SigninResponse)
async def signin(request: SigninRequest) -> SigninResponse:
    session = create_session(request.username, request.password, request.site_content_url)
    try:
        return SigninResponse(
            token=session.token,
            site_id=session.site_id,
            site_content_url=request.site_content_url,
        )
    finally:
        # This endpoint exists mainly so callers can verify credentials;
        # since we never persist the token, sign out immediately after
        # returning it to avoid leaving an orphaned Tableau session open.
        session.close()
