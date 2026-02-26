"""Auth endpoints: signup, email verification."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.account import SignupRequest, SignupResponse, VerifyEmailResponse
from app.services import account as account_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/signup",
    response_model=SignupResponse,
    status_code=200,
    dependencies=[Depends(check_rate_limit)],
)
async def signup(
    data: SignupRequest,
    db: AsyncSession = Depends(get_db),
) -> SignupResponse:
    """Request a verification email. Rate limited to 1/minute per IP."""
    await account_service.request_signup(db, data.email)
    return SignupResponse()


@router.get(
    "/verify-email",
    response_model=VerifyEmailResponse,
)
async def verify_email(
    token: str = Query(..., max_length=128),
    db: AsyncSession = Depends(get_db),
) -> VerifyEmailResponse:
    """Verify email via token link. Returns a one-time registration token."""
    registration_token, expires_in = await account_service.verify_email(db, token)
    return VerifyEmailResponse(
        registration_token=registration_token,
        expires_in_seconds=expires_in,
    )
