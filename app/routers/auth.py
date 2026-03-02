"""Auth endpoints: signup, email verification."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
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
    request: Request,
    token: str = Query(..., max_length=128),
    db: AsyncSession = Depends(get_db),
) -> VerifyEmailResponse | HTMLResponse:
    """Verify email via token link. Returns a one-time registration token."""
    registration_token, expires_in = await account_service.verify_email(db, token)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        expires_min = expires_in // 60
        html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Email Verified — Arcoa</title>
<style>
  body {{ margin:0; background:#111; color:#e0e0e0; font-family:system-ui,sans-serif;
         display:flex; justify-content:center; align-items:center; min-height:100vh; }}
  .card {{ background:#1a1a1a; border:1px solid #333; border-radius:12px;
           max-width:520px; width:90%; padding:2.5rem; }}
  h1 {{ color:#4ade80; margin:0 0 .5rem; font-size:1.5rem; }}
  p {{ line-height:1.6; margin:.75rem 0; }}
  .token {{ background:#0d0d0d; border:1px solid #333; border-radius:8px;
            padding:1rem; font-family:monospace; font-size:.85rem;
            word-break:break-all; margin:1rem 0; }}
  .label {{ color:#888; font-size:.8rem; text-transform:uppercase; letter-spacing:.05em; }}
  .note {{ color:#888; font-size:.85rem; margin-top:1.5rem; }}
</style>
</head>
<body>
<div class="card">
  <h1>Email Verified</h1>
  <p>Your email has been verified. Use the registration token below to register your agent via <code>POST /agents</code>.</p>
  <div class="label">Registration Token</div>
  <div class="token">{registration_token}</div>
  <div class="label">Expires</div>
  <p>{expires_min} minutes</p>
  <div class="note">
    <strong>Next steps:</strong> Include this token as <code>registration_token</code> in your
    <code>POST /agents</code> request body to complete agent registration.
  </div>
</div>
</body>
</html>"""
        return HTMLResponse(content=html)

    return VerifyEmailResponse(
        registration_token=registration_token,
        expires_in_seconds=expires_in,
    )
