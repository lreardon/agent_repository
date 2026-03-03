"""Auth endpoints: signup, email verification, key recovery."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.rate_limit import check_rate_limit
from app.database import get_db
from app.schemas.account import (
    RecoveryRequest,
    RecoveryResponse,
    RotateKeyRequest,
    RotateKeyResponse,
    SignupRequest,
    SignupResponse,
    VerifyEmailResponse,
    VerifyRecoveryResponse,
)
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
        cli_command = f'arcoa init --name "YourAgent" --token {registration_token}'
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
            word-break:break-all; margin:1rem 0; display:flex;
            align-items:center; justify-content:space-between; gap:.5rem; }}
  .token span {{ flex:1; }}
  .copy-btn {{ background:#333; color:#e0e0e0; border:1px solid #555; border-radius:4px;
               padding:.3rem .6rem; cursor:pointer; font-size:.75rem; white-space:nowrap; }}
  .copy-btn:hover {{ background:#444; }}
  .label {{ color:#888; font-size:.8rem; text-transform:uppercase; letter-spacing:.05em; }}
  .note {{ color:#888; font-size:.85rem; margin-top:1.5rem; }}
</style>
</head>
<body>
<div class="card">
  <h1>Email Verified</h1>
  <p>Your email has been verified. Use the registration token below to register your agent.</p>
  <div class="label">Registration Token</div>
  <div class="token"><span id="token-text">{registration_token}</span><button class="copy-btn" onclick="copyText('{registration_token}', this)">Copy</button></div>
  <div class="label">Expires</div>
  <p>{expires_min} minutes</p>
  <div class="label">CLI Command</div>
  <div class="token"><span id="cli-text">{cli_command}</span><button class="copy-btn" onclick="copyText(`{cli_command}`, this)">Copy</button></div>
  <div class="note">
    <strong>Next steps:</strong> Run the CLI command above, or include the token as
    <code>registration_token</code> in your <code>POST /agents</code> request body.
  </div>
</div>
<script>
function copyText(text, btn) {{
  navigator.clipboard.writeText(text).then(function() {{
    btn.textContent = 'Copied!';
    setTimeout(function() {{ btn.textContent = 'Copy'; }}, 2000);
  }});
}}
</script>
</body>
</html>"""
        return HTMLResponse(content=html)

    return VerifyEmailResponse(
        registration_token=registration_token,
        expires_in_seconds=expires_in,
    )


# ---------------------------------------------------------------------------
# Key Recovery
# ---------------------------------------------------------------------------


@router.post(
    "/recover",
    response_model=RecoveryResponse,
    status_code=200,
    dependencies=[Depends(check_rate_limit)],
)
async def recover(
    data: RecoveryRequest,
    db: AsyncSession = Depends(get_db),
) -> RecoveryResponse:
    """Request a key recovery email. Rate limited to 1/minute per IP."""
    await account_service.request_recovery(db, data.email)
    return RecoveryResponse()


@router.get(
    "/verify-recovery",
    response_model=VerifyRecoveryResponse,
)
async def verify_recovery(
    request: Request,
    token: str = Query(..., max_length=128),
    db: AsyncSession = Depends(get_db),
) -> VerifyRecoveryResponse | HTMLResponse:
    """Verify recovery email token. Returns a one-time recovery token."""
    recovery_token, expires_in = await account_service.verify_recovery(db, token)

    accept = request.headers.get("accept", "")
    if "text/html" in accept:
        expires_min = expires_in // 60
        html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Recovery Verified — Arcoa</title>
<style>
  body {{ margin:0; background:#111; color:#e0e0e0; font-family:system-ui,sans-serif;
         display:flex; justify-content:center; align-items:center; min-height:100vh; }}
  .card {{ background:#1a1a1a; border:1px solid #333; border-radius:12px;
           max-width:520px; width:90%; padding:2.5rem; }}
  h1 {{ color:#f59e0b; margin:0 0 .5rem; font-size:1.5rem; }}
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
  <h1>Recovery Verified</h1>
  <p>Your identity has been verified. Use the recovery token below to rotate your agent's public key via <code>POST /auth/rotate-key</code>.</p>
  <div class="label">Recovery Token</div>
  <div class="token">{recovery_token}</div>
  <div class="label">Expires</div>
  <p>{expires_min} minutes</p>
  <div class="note">
    <strong>Next steps:</strong> Send a <code>POST /auth/rotate-key</code> request with
    <code>recovery_token</code> and your <code>new_public_key</code>.
  </div>
</div>
</body>
</html>"""
        return HTMLResponse(content=html)

    return VerifyRecoveryResponse(
        recovery_token=recovery_token,
        expires_in_seconds=expires_in,
    )


@router.post(
    "/rotate-key",
    response_model=RotateKeyResponse,
    status_code=200,
)
async def rotate_key(
    data: RotateKeyRequest,
    db: AsyncSession = Depends(get_db),
) -> RotateKeyResponse:
    """Rotate agent public key using a valid recovery token."""
    await account_service.rotate_key(db, data.recovery_token, data.new_public_key)
    return RotateKeyResponse()
