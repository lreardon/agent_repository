"""Tests for the Admin API."""

import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentStatus
from app.models.account import Account
from app.models.escrow import EscrowAccount, EscrowStatus, EscrowAuditLog
from app.models.job import Job, JobStatus
from app.models.wallet import (
    DepositTransaction, DepositStatus,
    WithdrawalRequest, WithdrawalStatus,
)
from app.models.webhook import WebhookDelivery, WebhookStatus
from tests.conftest import make_agent_data


ADMIN_KEY = "test-admin-key-12345"
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}

# Use the configured admin path prefix (may differ from "/admin")
from app.config import settings as _settings
ADMIN_PREFIX = _settings.admin_path_prefix


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def admin_client(client: AsyncClient):
    """Client with admin API keys enabled."""
    with patch("app.auth.admin.settings") as mock_settings:
        mock_settings.admin_api_keys = ADMIN_KEY
        yield client


@pytest_asyncio.fixture
async def sample_agent(db_session: AsyncSession) -> Agent:
    """Create a sample agent in the DB."""
    agent = Agent(
        public_key="admin_test_pk_" + uuid.uuid4().hex[:16],
        display_name="Admin Test Agent",
        description="For admin tests",
        endpoint_url="https://example.com/agent",
        capabilities=["test"],
        webhook_secret="secret123",
        balance=Decimal("100.00"),
        status=AgentStatus.ACTIVE,
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest_asyncio.fixture
async def sample_account(db_session: AsyncSession, sample_agent: Agent) -> Account:
    """Create a sample account linked to the agent."""
    account = Account(
        email="admin-test@example.com",
        email_verified=True,
        agent_id=sample_agent.agent_id,
    )
    db_session.add(account)
    await db_session.flush()
    return account


@pytest_asyncio.fixture
async def two_agents(db_session: AsyncSession) -> tuple[Agent, Agent]:
    """Create two agents for job/escrow tests."""
    client_agent = Agent(
        public_key="admin_client_pk_" + uuid.uuid4().hex[:16],
        display_name="Client Agent",
        webhook_secret="secret1",
        balance=Decimal("500.00"),
    )
    seller_agent = Agent(
        public_key="admin_seller_pk_" + uuid.uuid4().hex[:16],
        display_name="Seller Agent",
        webhook_secret="secret2",
        balance=Decimal("200.00"),
    )
    db_session.add_all([client_agent, seller_agent])
    await db_session.flush()
    return client_agent, seller_agent


@pytest_asyncio.fixture
async def sample_job(db_session: AsyncSession, two_agents: tuple[Agent, Agent]) -> Job:
    """Create a sample job."""
    client, seller = two_agents
    job = Job(
        client_agent_id=client.agent_id,
        seller_agent_id=seller.agent_id,
        status=JobStatus.IN_PROGRESS,
        agreed_price=Decimal("50.00"),
    )
    db_session.add(job)
    await db_session.flush()
    return job


@pytest_asyncio.fixture
async def sample_escrow(
    db_session: AsyncSession, two_agents: tuple[Agent, Agent], sample_job: Job
) -> EscrowAccount:
    """Create a funded escrow."""
    client, seller = two_agents
    escrow = EscrowAccount(
        job_id=sample_job.job_id,
        client_agent_id=client.agent_id,
        seller_agent_id=seller.agent_id,
        amount=Decimal("50.00"),
        seller_bond_amount=Decimal("10.00"),
        status=EscrowStatus.FUNDED,
    )
    db_session.add(escrow)
    await db_session.flush()
    return escrow


@pytest_asyncio.fixture
async def sample_webhook(db_session: AsyncSession, sample_agent: Agent) -> WebhookDelivery:
    """Create a sample webhook delivery."""
    delivery = WebhookDelivery(
        target_agent_id=sample_agent.agent_id,
        event_type="job.completed",
        payload={"test": True},
        status=WebhookStatus.FAILED,
        attempts=3,
        last_error="Connection refused",
    )
    db_session.add(delivery)
    await db_session.flush()
    return delivery


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------

class TestAdminAuth:
    """Test admin authentication."""

    @pytest.mark.asyncio
    async def test_no_admin_key_header(self, client: AsyncClient):
        """Missing X-Admin-Key returns 404 (hides admin existence)."""
        with patch("app.auth.admin.settings") as m:
            m.admin_api_keys = ADMIN_KEY
            resp = await client.get(f"{ADMIN_PREFIX}/stats")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_admin_key(self, client: AsyncClient):
        """Wrong key returns 404 (hides admin existence)."""
        with patch("app.auth.admin.settings") as m:
            m.admin_api_keys = ADMIN_KEY
            resp = await client.get(f"{ADMIN_PREFIX}/stats", headers={"X-Admin-Key": "wrong"})
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_disabled(self, client: AsyncClient):
        """Empty admin_api_keys returns 404 (hides admin existence)."""
        with patch("app.auth.admin.settings") as m:
            m.admin_api_keys = ""
            resp = await client.get(f"{ADMIN_PREFIX}/stats", headers=ADMIN_HEADERS)
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_valid_admin_key(self, admin_client: AsyncClient):
        """Valid key succeeds."""
        resp = await admin_client.get(f"{ADMIN_PREFIX}/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestPlatformStats:

    @pytest.mark.asyncio
    async def test_empty_stats(self, admin_client: AsyncClient):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_agents"] == 0
        assert data["total_jobs"] == 0

    @pytest.mark.asyncio
    async def test_stats_with_data(self, admin_client: AsyncClient, sample_agent: Agent, sample_account: Account):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/stats", headers=ADMIN_HEADERS)
        data = resp.json()
        assert data["total_agents"] >= 1
        assert data["active_agents"] >= 1
        assert data["total_accounts"] >= 1
        assert data["verified_accounts"] >= 1


# ---------------------------------------------------------------------------
# Agents
# ---------------------------------------------------------------------------

class TestAdminAgents:

    @pytest.mark.asyncio
    async def test_list_agents(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/agents", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["items"]) >= 1

    @pytest.mark.asyncio
    async def test_list_agents_filter_status(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/agents?status=active", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["status"] == "active"

    @pytest.mark.asyncio
    async def test_list_agents_search(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/agents?search=Admin+Test", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_get_agent_detail(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["display_name"] == "Admin Test Agent"
        assert data["public_key"].startswith("admin_test_pk_")

    @pytest.mark.asyncio
    async def test_get_agent_not_found(self, admin_client: AsyncClient):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/agents/{uuid.uuid4()}", headers=ADMIN_HEADERS)
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_suspend_agent(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}/status",
            headers=ADMIN_HEADERS,
            json={"status": "suspended", "reason": "Violation of TOS"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "suspended"

    @pytest.mark.asyncio
    async def test_reactivate_agent(self, admin_client: AsyncClient, sample_agent: Agent, db_session: AsyncSession):
        sample_agent.status = AgentStatus.SUSPENDED
        await db_session.flush()

        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}/status",
            headers=ADMIN_HEADERS,
            json={"status": "active", "reason": "Appeal approved"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_invalid_status(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}/status",
            headers=ADMIN_HEADERS,
            json={"status": "bogus"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_adjust_balance_positive(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}/balance?amount=25.00&reason=Compensation",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["balance"]) == Decimal("125.00")

    @pytest.mark.asyncio
    async def test_adjust_balance_negative(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}/balance?amount=-50.00&reason=Penalty",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        assert Decimal(resp.json()["balance"]) == Decimal("50.00")

    @pytest.mark.asyncio
    async def test_adjust_balance_below_zero(self, admin_client: AsyncClient, sample_agent: Agent):
        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/agents/{sample_agent.agent_id}/balance?amount=-999.00",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class TestAdminJobs:

    @pytest.mark.asyncio
    async def test_list_jobs(self, admin_client: AsyncClient, sample_job: Job):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/jobs", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_jobs_filter_status(self, admin_client: AsyncClient, sample_job: Job):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/jobs?status=in_progress", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_list_jobs_filter_agent(self, admin_client: AsyncClient, sample_job: Job, two_agents):
        client_agent, _ = two_agents
        resp = await admin_client.get(
            f"{ADMIN_PREFIX}/jobs?agent_id={client_agent.agent_id}", headers=ADMIN_HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_get_job_detail(self, admin_client: AsyncClient, sample_job: Job):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/jobs/{sample_job.job_id}", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_force_cancel_job(self, admin_client: AsyncClient, sample_job: Job):
        resp = await admin_client.patch(
            f"{ADMIN_PREFIX}/jobs/{sample_job.job_id}/status",
            headers=ADMIN_HEADERS,
            json={"status": "cancelled", "reason": "Admin intervention"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Escrow
# ---------------------------------------------------------------------------

class TestAdminEscrow:

    @pytest.mark.asyncio
    async def test_list_escrows(self, admin_client: AsyncClient, sample_escrow: EscrowAccount):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/escrow", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_force_refund(
        self, admin_client: AsyncClient, sample_escrow: EscrowAccount, two_agents, db_session: AsyncSession
    ):
        client_agent, seller_agent = two_agents
        client_balance_before = client_agent.balance
        seller_balance_before = seller_agent.balance

        resp = await admin_client.post(
            f"{ADMIN_PREFIX}/escrow/{sample_escrow.escrow_id}/force-refund",
            headers=ADMIN_HEADERS,
            json={"reason": "Dispute resolution"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "refunded"

        await db_session.refresh(client_agent)
        await db_session.refresh(seller_agent)
        assert client_agent.balance == client_balance_before + Decimal("50.00")
        assert seller_agent.balance == seller_balance_before + Decimal("10.00")

    @pytest.mark.asyncio
    async def test_force_refund_not_funded(
        self, admin_client: AsyncClient, sample_escrow: EscrowAccount, db_session: AsyncSession
    ):
        sample_escrow.status = EscrowStatus.RELEASED
        await db_session.flush()

        resp = await admin_client.post(
            f"{ADMIN_PREFIX}/escrow/{sample_escrow.escrow_id}/force-refund",
            headers=ADMIN_HEADERS,
            json={"reason": "Test"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class TestAdminAccounts:

    @pytest.mark.asyncio
    async def test_list_accounts(self, admin_client: AsyncClient, sample_account: Account):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/accounts", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_accounts_filter_verified(self, admin_client: AsyncClient, sample_account: Account):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/accounts?verified=true", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["email_verified"] is True

    @pytest.mark.asyncio
    async def test_list_accounts_search(self, admin_client: AsyncClient, sample_account: Account):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/accounts?search=admin-test", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class TestAdminWebhooks:

    @pytest.mark.asyncio
    async def test_list_webhooks(self, admin_client: AsyncClient, sample_webhook: WebhookDelivery):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/webhooks", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["total"] >= 1

    @pytest.mark.asyncio
    async def test_list_webhooks_filter_status(self, admin_client: AsyncClient, sample_webhook: WebhookDelivery):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/webhooks?status=failed", headers=ADMIN_HEADERS)
        assert resp.status_code == 200
        for item in resp.json()["items"]:
            assert item["status"] == "failed"

    @pytest.mark.asyncio
    async def test_redeliver_webhook(self, admin_client: AsyncClient, sample_webhook: WebhookDelivery):
        resp = await admin_client.post(
            f"{ADMIN_PREFIX}/webhooks/{sample_webhook.delivery_id}/redeliver",
            headers=ADMIN_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["attempts"] == 0
        assert data["last_error"] is None


# ---------------------------------------------------------------------------
# V1 prefix
# ---------------------------------------------------------------------------

class TestAdminObfuscatedPrefix:
    """Ensure admin endpoints use the configured (obfuscated) path prefix."""

    @pytest.mark.asyncio
    async def test_admin_at_configured_prefix(self, admin_client: AsyncClient):
        resp = await admin_client.get(f"{ADMIN_PREFIX}/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_admin_not_at_plain_path(self, admin_client: AsyncClient):
        """Admin endpoints are NOT at /admin/ — obfuscated prefix only."""
        resp = await admin_client.get("/admin/stats", headers=ADMIN_HEADERS)
        assert resp.status_code == 404
