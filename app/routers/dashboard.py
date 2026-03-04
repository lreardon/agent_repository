"""Human dashboard — server-rendered HTML for agent owners."""

import logging
import secrets
from datetime import UTC, datetime, timedelta
from html import escape

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.account import Account
from app.models.agent import Agent, AgentStatus
from app.models.job import Job, JobStatus
from app.models.webhook import WebhookDelivery

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dashboard"])

DASHBOARD_TOKEN_EXPIRY = timedelta(hours=1)


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

async def _get_account_by_dashboard_token(
    db: AsyncSession, token: str
) -> Account | None:
    """Look up account by dashboard token. Returns None if invalid/expired."""
    result = await db.execute(
        select(Account).where(Account.dashboard_token == token)
    )
    account = result.scalar_one_or_none()
    if account is None:
        return None
    if account.dashboard_token_expires_at is None or datetime.now(UTC) > account.dashboard_token_expires_at:
        return None
    return account


async def _refresh_dashboard_token(db: AsyncSession, account: Account) -> str:
    """Issue or refresh a dashboard token. Returns the token."""
    token = secrets.token_urlsafe(48)
    account.dashboard_token = token
    account.dashboard_token_expires_at = datetime.now(UTC) + DASHBOARD_TOKEN_EXPIRY
    await db.flush()
    return token


async def _issue_dashboard_token_for_email(db: AsyncSession, email: str) -> str | None:
    """Issue a dashboard token for an account by email. Returns token or None."""
    result = await db.execute(
        select(Account).where(Account.email == email, Account.email_verified.is_(True))
    )
    account = result.scalar_one_or_none()
    if account is None or account.agent_id is None:
        return None
    token = await _refresh_dashboard_token(db, account)
    await db.commit()
    return token


# ---------------------------------------------------------------------------
# Dashboard data API (JSON — used by JS polling)
# ---------------------------------------------------------------------------

@router.get("/dashboard/api/data", include_in_schema=False)
async def dashboard_data(
    token: str = Query(..., max_length=128),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """JSON endpoint for dashboard polling — returns agent data."""
    account = await _get_account_by_dashboard_token(db, token)
    if account is None:
        return JSONResponse(status_code=401, content={"detail": "Session expired"})

    agent = await db.get(Agent, account.agent_id)
    if agent is None:
        return JSONResponse(status_code=404, content={"detail": "Agent not found"})

    # Recent webhooks (last 10)
    wh_result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.target_agent_id == agent.agent_id)
        .order_by(WebhookDelivery.created_at.desc())
        .limit(10)
    )
    webhooks = [
        {
            "delivery_id": str(w.delivery_id),
            "event_type": w.event_type,
            "status": w.status.value,
            "attempts": w.attempts,
            "last_error": w.last_error,
            "created_at": w.created_at.isoformat(),
        }
        for w in wh_result.scalars().all()
    ]

    # Recent jobs (last 5)
    job_result = await db.execute(
        select(Job)
        .where((Job.client_agent_id == agent.agent_id) | (Job.seller_agent_id == agent.agent_id))
        .order_by(Job.updated_at.desc())
        .limit(5)
    )
    jobs = []
    for j in job_result.scalars().all():
        counterparty_id = j.seller_agent_id if j.client_agent_id == agent.agent_id else j.client_agent_id
        counterparty = await db.get(Agent, counterparty_id)
        jobs.append({
            "job_id": str(j.job_id),
            "status": j.status.value,
            "role": "client" if j.client_agent_id == agent.agent_id else "seller",
            "counterparty": counterparty.display_name if counterparty else str(counterparty_id),
            "agreed_price": str(j.agreed_price) if j.agreed_price else None,
            "updated_at": j.updated_at.isoformat(),
        })

    # Refresh token expiry
    await _refresh_dashboard_token(db, account)
    await db.commit()

    return JSONResponse(content={
        "agent": {
            "agent_id": str(agent.agent_id),
            "display_name": agent.display_name,
            "status": agent.status.value,
            "balance": str(agent.balance),
            "reputation_seller": str(agent.reputation_seller),
            "reputation_client": str(agent.reputation_client),
            "endpoint_url": agent.endpoint_url,
            "capabilities": agent.capabilities or [],
            "is_online": agent.is_online,
            "created_at": agent.created_at.isoformat(),
            "last_seen": agent.last_seen.isoformat(),
        },
        "webhooks": webhooks,
        "jobs": jobs,
    })


# ---------------------------------------------------------------------------
# Deactivate action
# ---------------------------------------------------------------------------

@router.post("/dashboard/api/deactivate", include_in_schema=False)
async def dashboard_deactivate(
    token: str = Query(..., max_length=128),
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    """Deactivate the agent from the dashboard."""
    account = await _get_account_by_dashboard_token(db, token)
    if account is None:
        return JSONResponse(status_code=401, content={"detail": "Session expired"})

    agent = await db.get(Agent, account.agent_id)
    if agent is None:
        return JSONResponse(status_code=404, content={"detail": "Agent not found"})

    if agent.status == AgentStatus.DEACTIVATED:
        return JSONResponse(status_code=400, content={"detail": "Agent is already deactivated"})

    agent.status = AgentStatus.DEACTIVATED
    account.agent_id = None  # Unlink so they can register a new agent
    account.dashboard_token = None
    account.dashboard_token_expires_at = None
    await db.commit()

    logger.info("Agent %s deactivated via dashboard by %s", agent.agent_id, account.email)

    return JSONResponse(content={"status": "deactivated", "agent_id": str(agent.agent_id)})


# ---------------------------------------------------------------------------
# Main dashboard page (server-rendered HTML)
# ---------------------------------------------------------------------------

@router.get("/dashboard", include_in_schema=False)
async def dashboard(
    request: Request,
    token: str = Query(None, max_length=128),
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Render the agent owner dashboard."""

    if not token:
        return _login_page()

    account = await _get_account_by_dashboard_token(db, token)
    if account is None:
        return _login_page(error="Session expired. Please log in again.")

    agent = await db.get(Agent, account.agent_id)
    if agent is None:
        return _login_page(error="No agent linked to this account.")

    # Refresh token
    new_token = await _refresh_dashboard_token(db, account)
    await db.commit()

    return _dashboard_page(agent, new_token)


# ---------------------------------------------------------------------------
# Dashboard login (email → sends link)
# ---------------------------------------------------------------------------

@router.post("/dashboard/login", include_in_schema=False)
async def dashboard_login(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Send a dashboard login link to the user's email."""
    form = await request.form()
    email = str(form.get("email", "")).strip().lower()

    if not email or "@" not in email:
        return _login_page(error="Please enter a valid email address.")

    # Look up account
    result = await db.execute(
        select(Account).where(Account.email == email, Account.email_verified.is_(True))
    )
    account = result.scalar_one_or_none()

    if account is None or account.agent_id is None:
        # Don't leak whether the email exists
        return _login_page(success="If an account exists for that email, a login link has been sent.")

    # Issue token and send email
    token = await _refresh_dashboard_token(db, account)
    await db.commit()

    dashboard_url = f"{settings.base_url}/dashboard?token={token}"
    body = (
        f"Click the link below to access your agent dashboard:\n\n"
        f"{dashboard_url}\n\n"
        f"This link expires in 1 hour.\n\n"
        f"If you did not request this, ignore this email."
    )

    try:
        from app.services.email import get_email_sender, make_html_email
        sender = get_email_sender()
        await sender.send(
            to=email,
            subject="Arcoa — Dashboard Login",
            body=body,
            from_name="Arcoa",
            html=make_html_email(body),
        )
    except Exception:
        logger.exception("Failed to send dashboard login email to %s", email)

    return _login_page(success="If an account exists for that email, a login link has been sent.")


# ---------------------------------------------------------------------------
# HTML templates
# ---------------------------------------------------------------------------

_BASE_STYLE = """\
  body { margin:0; background:#111; color:#e0e0e0; font-family:system-ui,-apple-system,sans-serif; }
  * { box-sizing:border-box; }
  a { color:#c9a962; text-decoration:none; }
  a:hover { text-decoration:underline; }
  .container { max-width:900px; margin:0 auto; padding:1.5rem; }
  .card { background:#1a1a1a; border:1px solid #333; border-radius:12px; padding:1.5rem; margin-bottom:1rem; }
  h1 { color:#fff; margin:0 0 .25rem; font-size:1.5rem; }
  h2 { color:#fff; margin:0 0 .75rem; font-size:1.1rem; }
  .subtitle { color:#888; font-size:.9rem; margin-bottom:1.5rem; }
  .status-badge { display:inline-block; padding:.2rem .6rem; border-radius:4px; font-size:.8rem;
                  font-weight:600; text-transform:uppercase; letter-spacing:.05em; }
  .status-active { background:#166534; color:#4ade80; }
  .status-suspended { background:#92400e; color:#fbbf24; }
  .status-deactivated { background:#7f1d1d; color:#f87171; }
  .online-dot { display:inline-block; width:8px; height:8px; border-radius:50%;
                margin-right:6px; vertical-align:middle; }
  .online-dot.on { background:#4ade80; }
  .online-dot.off { background:#666; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:1rem; }
  .stat { }
  .stat .label { color:#888; font-size:.75rem; text-transform:uppercase; letter-spacing:.05em; }
  .stat .value { font-size:1.25rem; color:#fff; margin-top:.25rem; font-family:'IBM Plex Mono',monospace; }
  table { width:100%; border-collapse:collapse; font-size:.85rem; }
  th { text-align:left; color:#888; font-size:.75rem; text-transform:uppercase; letter-spacing:.05em;
       padding:.5rem .75rem; border-bottom:1px solid #333; }
  td { padding:.5rem .75rem; border-bottom:1px solid #222; vertical-align:top; }
  .mono { font-family:'IBM Plex Mono',monospace; font-size:.8rem; color:#ccc; }
  .tag { display:inline-block; background:#222; border:1px solid #444; border-radius:4px;
         padding:.15rem .4rem; font-size:.75rem; margin:.1rem .2rem .1rem 0; color:#ccc; }
  .btn { background:#333; color:#e0e0e0; border:1px solid #555; border-radius:6px;
         padding:.5rem 1rem; cursor:pointer; font-size:.85rem; }
  .btn:hover { background:#444; }
  .btn-danger { background:#7f1d1d; border-color:#991b1b; color:#fca5a5; }
  .btn-danger:hover { background:#991b1b; }
  .empty { color:#666; font-style:italic; padding:1rem 0; }
  .error { background:#7f1d1d; border:1px solid #991b1b; color:#fca5a5; padding:.75rem 1rem;
           border-radius:8px; margin-bottom:1rem; }
  .success { background:#14532d; border:1px solid #166534; color:#4ade80; padding:.75rem 1rem;
             border-radius:8px; margin-bottom:1rem; }
  .header { display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem;
            margin-bottom:1.5rem; }
  .header-right { display:flex; gap:.5rem; align-items:center; }
  .refresh-indicator { color:#666; font-size:.75rem; }
  @media (max-width:600px) {
    .container { padding:1rem; }
    .grid { grid-template-columns:1fr 1fr; }
    table { font-size:.8rem; }
    th, td { padding:.4rem .5rem; }
    .header { flex-direction:column; align-items:flex-start; }
  }
"""


def _login_page(error: str = "", success: str = "") -> HTMLResponse:
    """Render the dashboard login page."""
    error_html = f'<div class="error">{escape(error)}</div>' if error else ""
    success_html = f'<div class="success">{escape(success)}</div>' if success else ""

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Dashboard Login — Arcoa</title>
<style>{_BASE_STYLE}
  .login-wrap {{ display:flex; justify-content:center; align-items:center; min-height:100vh; }}
  .login-card {{ max-width:420px; width:90%; }}
  input[type=email] {{ width:100%; background:#0d0d0d; border:1px solid #333; border-radius:6px;
                       padding:.6rem .75rem; color:#e0e0e0; font-size:.95rem; margin:.75rem 0; }}
  input[type=email]:focus {{ outline:none; border-color:#c9a962; }}
  .login-btn {{ width:100%; background:#c9a962; color:#111; border:none; border-radius:6px;
                padding:.6rem; cursor:pointer; font-size:.95rem; font-weight:600; }}
  .login-btn:hover {{ background:#d4b572; }}
</style>
</head>
<body>
<div class="login-wrap">
  <div class="card login-card">
    <h1>Agent Dashboard</h1>
    <p class="subtitle">Enter your email to receive a login link.</p>
    {error_html}{success_html}
    <form method="POST" action="/dashboard/login">
      <input type="email" name="email" placeholder="you@example.com" required autofocus>
      <button type="submit" class="login-btn">Send Login Link</button>
    </form>
  </div>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


def _dashboard_page(agent: Agent, token: str) -> HTMLResponse:
    """Render the main dashboard page."""
    status_class = {
        AgentStatus.ACTIVE: "status-active",
        AgentStatus.SUSPENDED: "status-suspended",
        AgentStatus.DEACTIVATED: "status-deactivated",
    }.get(agent.status, "")

    online_class = "on" if agent.is_online else "off"
    online_text = "Online" if agent.is_online else "Offline"

    caps_html = ""
    if agent.capabilities:
        caps_html = " ".join(f'<span class="tag">{escape(c)}</span>' for c in agent.capabilities)
    else:
        caps_html = '<span class="empty">None</span>'

    html = f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(agent.display_name)} — Arcoa Dashboard</title>
<style>{_BASE_STYLE}</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <div class="header">
    <div>
      <h1>{escape(agent.display_name)}</h1>
      <div class="subtitle">
        <span class="online-dot {online_class}"></span>{online_text} ·
        <span class="status-badge {status_class}">{agent.status.value}</span>
      </div>
    </div>
    <div class="header-right">
      <span class="refresh-indicator" id="refresh-timer">Refreshes in 30s</span>
      <button class="btn btn-danger" id="deactivate-btn" {'' if agent.status == AgentStatus.ACTIVE else 'disabled'}>
        Deactivate Agent
      </button>
    </div>
  </div>

  <!-- Stats -->
  <div class="card">
    <div class="grid">
      <div class="stat">
        <div class="label">Balance</div>
        <div class="value" id="stat-balance">${{agent.balance}}</div>
      </div>
      <div class="stat">
        <div class="label">Seller Rep</div>
        <div class="value" id="stat-rep-seller">{agent.reputation_seller}</div>
      </div>
      <div class="stat">
        <div class="label">Client Rep</div>
        <div class="value" id="stat-rep-client">{agent.reputation_client}</div>
      </div>
      <div class="stat">
        <div class="label">Agent ID</div>
        <div class="value mono" style="font-size:.7rem; word-break:break-all;">{agent.agent_id}</div>
      </div>
    </div>
  </div>

  <!-- Details -->
  <div class="card">
    <h2>Configuration</h2>
    <div style="margin-bottom:.75rem;">
      <div class="label" style="color:#888; font-size:.75rem; text-transform:uppercase;">Endpoint</div>
      <div class="mono" style="margin-top:.25rem;">{escape(agent.endpoint_url or 'Not set')}</div>
    </div>
    <div>
      <div class="label" style="color:#888; font-size:.75rem; text-transform:uppercase;">Capabilities</div>
      <div style="margin-top:.25rem;">{caps_html}</div>
    </div>
  </div>

  <!-- Recent Jobs -->
  <div class="card">
    <h2>Recent Jobs</h2>
    <div id="jobs-table">
      <p class="empty">Loading...</p>
    </div>
  </div>

  <!-- Recent Webhooks -->
  <div class="card">
    <h2>Recent Webhook Deliveries</h2>
    <div id="webhooks-table">
      <p class="empty">Loading...</p>
    </div>
  </div>

  <div style="text-align:center; padding:2rem 0; color:#555; font-size:.75rem;">
    Registered {agent.created_at.strftime("%Y-%m-%d %H:%M UTC")} ·
    Last seen {agent.last_seen.strftime("%Y-%m-%d %H:%M UTC")}
  </div>

</div>

<script>
const TOKEN = "{token}";
const API = "/dashboard/api/data?token=" + TOKEN;
let countdown = 30;

function statusBadge(status) {{
  const cls = {{active:"status-active", suspended:"status-suspended",
                deactivated:"status-deactivated", completed:"status-active",
                failed:"status-deactivated", in_progress:"status-active",
                delivered:"status-active", funded:"status-active",
                proposed:"", cancelled:"status-deactivated"}}[status] || "";
  return '<span class="status-badge ' + cls + '">' + status + '</span>';
}}

function webhookStatusBadge(status) {{
  const cls = {{delivered:"status-active", failed:"status-deactivated", pending:""}}[status] || "";
  return '<span class="status-badge ' + cls + '">' + status + '</span>';
}}

function renderJobs(jobs) {{
  const el = document.getElementById("jobs-table");
  if (!jobs.length) {{ el.innerHTML = '<p class="empty">No jobs yet.</p>'; return; }}
  let html = '<table><thead><tr><th>Status</th><th>Role</th><th>Counterparty</th><th>Price</th><th>Updated</th></tr></thead><tbody>';
  for (const j of jobs) {{
    const dt = new Date(j.updated_at).toLocaleString();
    html += '<tr><td>' + statusBadge(j.status) + '</td><td>' + j.role + '</td><td>' +
            j.counterparty + '</td><td>' + (j.agreed_price ? '$' + j.agreed_price : '—') +
            '</td><td style="color:#888">' + dt + '</td></tr>';
  }}
  html += '</tbody></table>';
  el.innerHTML = html;
}}

function renderWebhooks(webhooks) {{
  const el = document.getElementById("webhooks-table");
  if (!webhooks.length) {{ el.innerHTML = '<p class="empty">No webhook deliveries yet.</p>'; return; }}
  let html = '<table><thead><tr><th>Event</th><th>Status</th><th>Attempts</th><th>Error</th><th>Time</th></tr></thead><tbody>';
  for (const w of webhooks) {{
    const dt = new Date(w.created_at).toLocaleString();
    const err = w.last_error ? '<span style="color:#f87171">' + w.last_error.substring(0,40) + '</span>' : '—';
    html += '<tr><td class="mono">' + w.event_type + '</td><td>' + webhookStatusBadge(w.status) +
            '</td><td>' + w.attempts + '</td><td>' + err + '</td><td style="color:#888">' + dt + '</td></tr>';
  }}
  html += '</tbody></table>';
  el.innerHTML = html;
}}

async function refresh() {{
  try {{
    const res = await fetch(API);
    if (res.status === 401) {{
      window.location.href = "/dashboard";
      return;
    }}
    const data = await res.json();
    // Update stats
    document.getElementById("stat-balance").textContent = "$" + data.agent.balance;
    document.getElementById("stat-rep-seller").textContent = data.agent.reputation_seller;
    document.getElementById("stat-rep-client").textContent = data.agent.reputation_client;
    renderJobs(data.jobs);
    renderWebhooks(data.webhooks);
  }} catch (e) {{
    console.error("Refresh failed:", e);
  }}
  countdown = 30;
}}

// Initial load
refresh();

// Poll every 30s
setInterval(refresh, 30000);
setInterval(function() {{
  countdown--;
  if (countdown < 0) countdown = 30;
  document.getElementById("refresh-timer").textContent = "Refreshes in " + countdown + "s";
}}, 1000);

// Deactivate
document.getElementById("deactivate-btn").addEventListener("click", async function() {{
  if (!confirm("Are you sure you want to deactivate your agent? This cannot be undone from the dashboard.")) return;
  if (!confirm("This will permanently deactivate your agent. Are you REALLY sure?")) return;
  try {{
    const res = await fetch("/dashboard/api/deactivate?token=" + TOKEN, {{ method: "POST" }});
    const data = await res.json();
    if (res.ok) {{
      alert("Agent deactivated successfully.");
      window.location.href = "/dashboard";
    }} else {{
      alert("Error: " + (data.detail || "Unknown error"));
    }}
  }} catch (e) {{
    alert("Request failed: " + e.message);
  }}
}});
</script>
</body>
</html>"""
    return HTMLResponse(content=html)
