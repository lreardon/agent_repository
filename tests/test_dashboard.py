"""Tests for the human dashboard."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.agent import Agent, AgentStatus
from app.models.job import Job, JobStatus
from app.models.webhook import WebhookDelivery, WebhookStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def dashboard_agent(db_session: AsyncSession) -> Agent:
    """Create an agent for dashboard tests."""
    agent = Agent(
        public_key="dash_pk_" + uuid.uuid4().hex[:16],
        display_name="Dashboard Test Agent",
        description="For dashboard tests",
        endpoint_url="https://example.com/agent",
        capabilities=["code-review", "testing"],
        webhook_secret="secret123",
        balance=Decimal("250.00"),
        reputation_seller=Decimal("4.50"),
        reputation_client=Decimal("4.80"),
        status=AgentStatus.ACTIVE,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def dashboard_account(db_session: AsyncSession, dashboard_agent: Agent) -> Account:
    """Create an account with a valid dashboard token."""
    token = secrets.token_urlsafe(48)
    account = Account(
        email="dashboard@example.com",
        email_verified=True,
        agent_id=dashboard_agent.agent_id,
        dashboard_token=token,
        dashboard_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(account)
    await db_session.flush()
    return account


@pytest_asyncio.fixture
async def dashboard_token(dashboard_account: Account) -> str:
    return dashboard_account.dashboard_token


@pytest_asyncio.fixture
async def sample_jobs(db_session: AsyncSession, dashboard_agent: Agent) -> list[Job]:
    """Create some jobs for the dashboard agent."""
    other_agent = Agent(
        public_key="dash_other_pk_" + uuid.uuid4().hex[:16],
        display_name="Other Agent",
        webhook_secret="secret456",
        balance=Decimal("100.00"),
    )
    db_session.add(other_agent)
    await db_session.flush()

    jobs = []
    for status in [JobStatus.COMPLETED, JobStatus.IN_PROGRESS, JobStatus.PROPOSED]:
        job = Job(
            client_agent_id=dashboard_agent.agent_id,
            seller_agent_id=other_agent.agent_id,
            status=status,
            agreed_price=Decimal("25.00"),
        )
        db_session.add(job)
        jobs.append(job)
    await db_session.flush()
    return jobs


@pytest_asyncio.fixture
async def sample_webhooks(db_session: AsyncSession, dashboard_agent: Agent) -> list[WebhookDelivery]:
    """Create some webhook deliveries."""
    deliveries = []
    for i, (evt, st) in enumerate([
        ("job.completed", WebhookStatus.DELIVERED),
        ("job.funded", WebhookStatus.FAILED),
        ("job.created", WebhookStatus.PENDING),
    ]):
        d = WebhookDelivery(
            target_agent_id=dashboard_agent.agent_id,
            event_type=evt,
            payload={"test": True},
            status=st,
            attempts=1 if st != WebhookStatus.PENDING else 0,
            last_error="Connection refused" if st == WebhookStatus.FAILED else None,
        )
        db_session.add(d)
        deliveries.append(d)
    await db_session.flush()
    return deliveries


# ---------------------------------------------------------------------------
# Dashboard page tests
# ---------------------------------------------------------------------------

class TestDashboardPage:

    @pytest.mark.asyncio
    async def test_no_token_shows_login(self, client: AsyncClient):
        resp = await client.get("/dashboard", follow_redirects=False)
        assert resp.status_code == 200
        assert "Agent Dashboard" in resp.text
        assert "Send Login Link" in resp.text

    @pytest.mark.asyncio
    async def test_invalid_token_shows_login(self, client: AsyncClient):
        resp = await client.get("/dashboard?token=bogus")
        assert resp.status_code == 200
        assert "Session expired" in resp.text

    @pytest.mark.asyncio
    async def test_expired_token_shows_login(self, client: AsyncClient, dashboard_account: Account, db_session: AsyncSession):
        dashboard_account.dashboard_token_expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db_session.flush()
        resp = await client.get(f"/dashboard?token={dashboard_account.dashboard_token}")
        assert resp.status_code == 200
        assert "Session expired" in resp.text

    @pytest.mark.asyncio
    async def test_valid_token_renders_dashboard(self, client: AsyncClient, dashboard_token: str, dashboard_agent: Agent):
        resp = await client.get(f"/dashboard?token={dashboard_token}")
        assert resp.status_code == 200
        assert dashboard_agent.display_name in resp.text
        assert "Deactivate Agent" in resp.text
        assert str(dashboard_agent.agent_id) in resp.text

    @pytest.mark.asyncio
    async def test_dashboard_shows_capabilities(self, client: AsyncClient, dashboard_token: str):
        resp = await client.get(f"/dashboard?token={dashboard_token}")
        assert "code-review" in resp.text
        assert "testing" in resp.text


# ---------------------------------------------------------------------------
# Data API tests
# ---------------------------------------------------------------------------

class TestDashboardDataAPI:

    @pytest.mark.asyncio
    async def test_data_invalid_token(self, client: AsyncClient):
        resp = await client.get("/dashboard/api/data?token=bogus")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_data_returns_agent(self, client: AsyncClient, dashboard_token: str, dashboard_agent: Agent):
        resp = await client.get(f"/dashboard/api/data?token={dashboard_token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent"]["display_name"] == "Dashboard Test Agent"
        assert data["agent"]["balance"] == "250.00"
        assert data["agent"]["status"] == "active"

    @pytest.mark.asyncio
    async def test_data_returns_webhooks(
        self, client: AsyncClient, dashboard_token: str, sample_webhooks
    ):
        resp = await client.get(f"/dashboard/api/data?token={dashboard_token}")
        data = resp.json()
        assert len(data["webhooks"]) == 3
        event_types = {w["event_type"] for w in data["webhooks"]}
        assert "job.completed" in event_types

    @pytest.mark.asyncio
    async def test_data_returns_jobs(
        self, client: AsyncClient, dashboard_token: str, sample_jobs
    ):
        resp = await client.get(f"/dashboard/api/data?token={dashboard_token}")
        data = resp.json()
        assert len(data["jobs"]) == 3
        assert all(j["role"] == "client" for j in data["jobs"])
        assert all(j["counterparty"] == "Other Agent" for j in data["jobs"])

    @pytest.mark.asyncio
    async def test_data_refreshes_token(
        self, client: AsyncClient, dashboard_token: str, dashboard_account: Account, db_session: AsyncSession
    ):
        old_expires = dashboard_account.dashboard_token_expires_at
        resp = await client.get(f"/dashboard/api/data?token={dashboard_token}")
        assert resp.status_code == 200
        await db_session.refresh(dashboard_account)
        # Token expiry should be extended
        assert dashboard_account.dashboard_token_expires_at > old_expires


# ---------------------------------------------------------------------------
# Deactivate tests
# ---------------------------------------------------------------------------

class TestDashboardDeactivate:

    @pytest.mark.asyncio
    async def test_deactivate_invalid_token(self, client: AsyncClient):
        resp = await client.post("/dashboard/api/deactivate?token=bogus")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_deactivate_agent(
        self, client: AsyncClient, dashboard_token: str,
        dashboard_agent: Agent, dashboard_account: Account, db_session: AsyncSession
    ):
        resp = await client.post(f"/dashboard/api/deactivate?token={dashboard_token}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deactivated"

        await db_session.refresh(dashboard_agent)
        assert dashboard_agent.status == AgentStatus.DEACTIVATED

        await db_session.refresh(dashboard_account)
        assert dashboard_account.agent_id is None
        assert dashboard_account.dashboard_token is None

    @pytest.mark.asyncio
    async def test_deactivate_already_deactivated(
        self, client: AsyncClient, dashboard_token: str, dashboard_agent: Agent, db_session: AsyncSession
    ):
        dashboard_agent.status = AgentStatus.DEACTIVATED
        await db_session.flush()
        resp = await client.post(f"/dashboard/api/deactivate?token={dashboard_token}")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Login flow tests
# ---------------------------------------------------------------------------

class TestDashboardLogin:

    @pytest.mark.asyncio
    async def test_login_form_renders(self, client: AsyncClient):
        resp = await client.get("/dashboard")
        assert resp.status_code == 200
        assert '<form method="POST"' in resp.text

    @pytest.mark.asyncio
    async def test_login_invalid_email(self, client: AsyncClient):
        resp = await client.post("/dashboard/login", data={"email": "not-an-email"})
        assert resp.status_code == 200
        assert "valid email" in resp.text

    @pytest.mark.asyncio
    async def test_login_unknown_email_no_leak(self, client: AsyncClient):
        resp = await client.post("/dashboard/login", data={"email": "unknown@example.com"})
        assert resp.status_code == 200
        assert "If an account exists" in resp.text

    @pytest.mark.asyncio
    async def test_login_known_email(self, client: AsyncClient, dashboard_account: Account):
        resp = await client.post(
            "/dashboard/login", data={"email": dashboard_account.email}
        )
        assert resp.status_code == 200
        assert "If an account exists" in resp.text
